import logging
import time

import aioboto3

from src.async_kinesis_client.utils import _sizeof

log = logging.getLogger(__name__)


# Following constants are originating from here:
# https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/kinesis.html#Kinesis.Client.put_records
MAX_RECORDS_IN_BATCH = 500
MAX_RECORD_SIZE = 1024 * 1024           # 1 Mb
MAX_BATCH_SIZE = 5 * MAX_RECORD_SIZE    # 5 Mb


def _get_default_partition_key():
    return '{0}{1}'.format(time.process_time(), time.time())


class AsyncKinesisProducer:

    def __init__(self, stream_name, ordered=True):

        self.stream_name = stream_name
        self.ordered = ordered

        self.seq = '0'

        self.record_buf = []
        self.buf_size = 0

        self.kinesis_client = aioboto3.client('kinesis')
        log.debug("Configured kinesis producer for stream '%s'; ordered=%s",
                  stream_name, ordered)



    async def put_record(self, record, partition_key=None, explicit_hash_key=None):
        """
        Put single record into Kinesis stream
        :param record:              record to put, bytes
        :param partition_key:       partition key to determine shard; if none, time-based key is used
        :param explicit_hash_key:   hash value used to determine the shard explicitly, overriding partition key
        :return:                    response from kinesis client, see boto3 doc
        """

        if partition_key is None:
            partition_key = _get_default_partition_key()

        kwargs = {
            'StreamName': self.stream_name,
            'Data': record,
            'PartitionKey': partition_key,
        }

        if self.ordered:
            kwargs['SequenceNumberForOrdering'] = self.seq

        kwargs['PartitionKey'] = partition_key or _get_default_partition_key()
        if explicit_hash_key:
            kwargs['ExplicitHashKey'] = explicit_hash_key

        resp = await self.kinesis_client.put_record(**kwargs)
        if self.ordered:
            self.seq = resp.get('SequenceNumber')
        return resp

    async def put_records(self, records, partition_key=None, explicit_hash_key=None):
        """
        Put list of records into Kinesis stream
        This call is buffered until it outgrow maximum allowed sizes (500 records or 5 Mb of data including partition
        keys) or until explicitly flushed (see flush() below)

        :param records:             iterable with records to put; records should be of bytes type
        :param partition_key:       partition key to determine shard; if none, time-based key is used
        :param explicit_hash_key:   hash value used to determine the shard explicitly, overriding partition key
        :return:                    Empty list if no records were flushed, list of responses from kinesis client
                                    otherwise

                                    Raises ValueError if single record exceeds 1 Mb
        """
        resp = []
        n = 1
        for r in records:

            if len(self.record_buf) == MAX_RECORDS_IN_BATCH:
                resp.append(await self.flush())

            record_size = _sizeof(r)

            # I hope I'm implementing this correctly, as there are different hints about maximum data sizes
            # in boto3 docs and general AWS docs
            if record_size > MAX_RECORD_SIZE:
                raise ValueError('Record # {} exceeded max record size of {}; size={}; record={}'.format(
                    n, MAX_RECORD_SIZE, record_size, r))

            datum = {}

            if explicit_hash_key :
                datum['ExplicitHashKey'] = explicit_hash_key
            else:
                datum['PartitionKey'] = partition_key or _get_default_partition_key()

            datum['Data'] = r
            datum_size = _sizeof(datum)

            if self.buf_size + datum_size > MAX_BATCH_SIZE:
                resp.append(await self.flush())

            self.record_buf.append(datum)
            self.buf_size += datum_size
            n += 1

        return resp

    async def flush(self):

        if len(self.record_buf) == 0:
            return

        resp = await self.kinesis_client.put_records(
            Records=self.record_buf,
            StreamName=self.stream_name
        )
        self.record_buf = []
        self.buf_size = 0
        return resp
