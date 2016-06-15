import pandas as pd
import numpy as np
import warnings


class BaseSerializer(object):

    sort_key = None
    required_fields = []
    datetime_fields = []

    def _sort_records(self, records):
        if self.sort_key is None:
            message = (
                'Must supply cls.sort_key in class definition.'
            )
            raise AttributeError(message)

        try:
            sorted_records = sorted(records, key=lambda x: x[self.sort_key])
        except KeyError:
            message = (
                'Sorting failed due to missing key {} in record.'
                .format(self.sort_key)
            )
            raise ValueError(message)

        return sorted_records

    def _validated_tuples_to_dataframe(self, validated_tuples):

        if validated_tuples == []:
            dts, values, estimateds = [], [], []
        else:
            dts, values, estimateds = zip(*validated_tuples)

        df = pd.DataFrame(
            {"value": values, "estimated": estimateds},
            index=pd.DatetimeIndex(dts),
            columns=["value", "estimated"],
        )
        df.value = df.value.astype(float)
        df.estimated = df.estimated.astype(bool)
        return df

    def to_dataframe(self, records):
        """
        Returns a dataframe of records.
        """
        sorted_records = self._sort_records(records)
        validated_tuples = list(self.yield_records(sorted_records))
        return self._validated_tuples_to_dataframe(validated_tuples)

    def yield_records(self, sorted_records):
        """
        Yields validated (start (datetime), value (float), estimated (bool))
        tuples of data.
        """
        raise NotImplementedError('`yield_records()` must be implemented.')

    def validate_record(self, record):

        # make sure required fields are available
        for field in self.required_fields:
            if field not in record:
                message = (
                    'Record missing "{}" field:\n{}'
                    .format(field, record)
                )
                raise ValueError(message)

        # make sure dates/datetimes are tz aware
        for field in self.datetime_fields:
            dt = record[field]
            if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
                message = (
                    'Record field ("{}": {}) is not timezone aware:\n{}'
                    .format(field, dt, record)
                )
                raise ValueError(message)

    def to_records(self, dataframe):
        raise NotImplementedError('`to_records()` must be implemented.')


class ArbitrarySerializer(BaseSerializer):
    '''
    Arbitrary data at arbitrary non-overlapping intervals.
    Often used for montly billing data. Records must all have
    the "start" key and the "end" key. Overlaps are not allowed and
    gaps will be filled with NaN.

    For example::

        records = [
            {
                "start": datetime(2013, 12, 30, tzinfo=pytz.utc),
                "end": datetime(2014, 1, 28, tzinfo=pytz.utc),
                "value": 1180,
            },
            {
                "start": datetime(2014, 1, 28, tzinfo=pytz.utc),
                "end": datetime(2014, 2, 27, tzinfo=pytz.utc),
                "value": 1211,
                "estimated": True,
            },
            {
                "start": datetime(2014, 2, 27, tzinfo=pytz.utc),
                "end": datetime(2014, 3, 30, tzinfo=pytz.utc),
                "value": 985,
            },
            {
                "start": datetime(2014, 3, 30, tzinfo=pytz.utc),
                "end": datetime(2014, 4, 25, tzinfo=pytz.utc),
                "value": 848,
            },
            {
                "start": datetime(2014, 4, 25, tzinfo=pytz.utc),
                "end": datetime(2014, 5, 27, tzinfo=pytz.utc),
                "value": 533,
            },
            ...
        ]
    '''

    sort_key = "start"
    required_fields = ["start", "end", "value"]
    datetime_fields = ["start", "end"]

    def validate_record(self, record):
        super(ArbitrarySerializer, self)\
            .validate_record(record)

        if record["start"] >= record["end"]:
            message = (
                'Record "start" must be earlier than record "end": {}\n'
                '{} >= {}.'.format(record, record["start"], record["end"])
            )
            raise ValueError(message)

    def yield_records(self, sorted_records):

        previous_end_datetime = None

        for record in sorted_records:

            self.validate_record(record)

            start = record["start"]
            end = record["end"]
            value = record["value"]
            estimated = record.get("estimated", False)

            if previous_end_datetime is None or start == previous_end_datetime:

                # normal record
                yield (start, value, estimated)
                previous_end_datetime = end

            elif start > previous_end_datetime:

                # blank record
                yield (previous_end_datetime, np.nan, False)

                # normal record
                yield (start, value, estimated)
                previous_end_datetime = end

            else:  # start < previous_end_datetime
                message = 'Skipping overlapping record: '\
                        'start ({}) < previous end ({})'\
                        .format(start, previous_end_datetime)
                warnings.warn(message)

        # final record carries last datetime, but only if there was a record
        if previous_end_datetime is not None:
            yield (previous_end_datetime, np.nan, False)

    def to_records(self, df):
        records = []
        for s, e, v, est in zip(df.index, df.index[1:],
                                df.value, df.estimated):
            records.append({
                "start": s.to_datetime(),
                "end": e.to_datetime(),
                "value": v,
                "estimated": bool(est),
            })
        return records


class ArbitraryStartSerializer(BaseSerializer):
    '''
    Arbitrary start data at arbitrary non-overlapping intervals.
    Records must all have the "start" key. The last data point
    will be ignored unless an end date is provided for it.
    This is useful for data dated to future energy use, e.g. billing for
    delivered fuels.

    For example::

        records = [
            {
                "start": datetime(2013, 12, 30, tzinfo=pytz.utc),
                "value": 1180,
            },
            {
                "start": datetime(2014, 1, 28, tzinfo=pytz.utc),
                "value": 1211,
            },
            {
                "start": datetime(2014, 2, 27, tzinfo=pytz.utc),
                "value": 985,
            },
            {
                "start": datetime(2014, 3, 30, tzinfo=pytz.utc),
                "value": 848,
            },
            {
                "start": datetime(2014, 4, 25, tzinfo=pytz.utc),
                "value": 533,
            },
            ...
        ]

    '''

    sort_key = "start"
    required_fields = ["start", "value"]
    datetime_fields = ["start"]

    def yield_records(self, sorted_records):

        n = len(sorted_records)
        for i, record in enumerate(sorted_records):

            self.validate_record(record)

            start = record["start"]
            value = record["value"]
            estimated = record.get("estimated", False)

            if i < n - 1:  # all except last record
                yield (start, value, estimated)
            else:  # last record
                end = record.get("end", None)
                if end is None:
                    # can't use the value of this record, no end date
                    yield (start, np.nan, False)
                else:
                    # provide an end date cap
                    if pd.notnull(value):
                        yield (start, value, estimated)
                        yield (end, np.nan, False)
                    else:
                        yield (start, np.nan, False)

    def to_records(self, df):
        records = []
        for i, row in df.iterrows():
            records.append({
                "start": i.to_datetime(),
                "value": row.value,
                "estimated": bool(row.estimated),
            })
        return records


class ArbitraryEndSerializer(BaseSerializer):
    '''

    Arbitrary end data at arbitrary non-overlapping intervals.
    Records must all have the "end" key. The first data point
    will be ignored unless a start date is provided for it.
    This is useful for data dated to past energy use, e.g. electricity
    or natural gas bills.

    Example::

        records = [
            {
                "end": datetime(2014, 1, 28, tzinfo=pytz.utc),
                "value": 1180,
            },
            {
                "end": datetime(2014, 2, 27, tzinfo=pytz.utc),
                "value": 1211,
            },
            {
                "end": datetime(2014, 3, 30, tzinfo=pytz.utc),
                "value": 985,
                "estimated": False,
            },
            {
                "end": datetime(2014, 4, 25, tzinfo=pytz.utc),
                "value": 848,
            },
            {
                "end": datetime(2014, 5, 27, tzinfo=pytz.utc),
                "value": 533,
            },
            ...
        ]

    '''

    sort_key = "end"
    required_fields = ["end", "value"]
    datetime_fields = ["end"]

    def yield_records(self, sorted_records):

        previous_end_datetime = None

        for record in sorted_records:

            self.validate_record(record)

            end = record["end"]
            value = record["value"]
            estimated = record.get("estimated", False)

            if previous_end_datetime is None:

                # first record, might have start
                start = record.get("start", None)

                if start is not None:
                    yield (start, value, estimated)
            else:
                yield (previous_end_datetime, value, estimated)

            previous_end_datetime = end

        if previous_end_datetime is not None:
            yield (previous_end_datetime, np.nan, estimated)

    def to_records(self, df):
        records = []

        if df.shape[0] > 0:
            records.append({
                "end": df.index[0].to_datetime(),
                "value": np.nan,
                "estimated": False,
            })

            for e, v, est in zip(df.index[1:], df.value, df.estimated):
                records.append({
                    "end": e.to_datetime(),
                    "value": v,
                    "estimated": bool(est),
                })

        return records
