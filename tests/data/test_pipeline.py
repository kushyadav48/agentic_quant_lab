"""Offline Phase 4 quality, causality, provenance and persistence tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import sqlite3
import pytest
from pydantic import ValidationError
from quantlab.data import (AssetClass, Instrument, MarketBar, MarketQuote,
    PriceType, Timeframe, TradingCalendar, VolumeType)
from quantlab.data.validation import (
    DataQualityReport, DuplicatePolicy, ValidationOptions,
    normalize_observations, validate_dataset,
)
from quantlab.data.resampling import MissingDataPolicy, ResampleRequest, resample
from quantlab.data.storage import (
    DatasetMetadata, ObservationType, SQLiteDatasetStore, StorageIntegrityError,
)

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)

@pytest.fixture
def instrument():
    return Instrument(instrument_id="EURUSD", symbol="EUR/USD", asset_class=AssetClass.FOREX,
        base_currency="EUR", quote_currency="USD", tick_size=Decimal("0.00001"),
        pip_size=Decimal("0.0001"), quantity_increment=Decimal("1"), lot_size=Decimal("100000"),
        calendar=TradingCalendar(calendar_id="fixture", timezone="UTC"))

def quote(seconds=0, bid="1.1", ask="1.2", **changes):
    fields = dict(instrument_id="EURUSD", source_id="fixture", dataset_id="raw-v1",
        timestamp=START + timedelta(seconds=seconds), available_at=START + timedelta(seconds=seconds),
        bid=Decimal(bid), ask=Decimal(ask))
    return MarketQuote(**(fields | changes))

def bar(minute=0, **changes):
    fields = dict(instrument_id="EURUSD", source_id="fixture", dataset_id="raw-bars-v1",
        timeframe=Timeframe.M1, price_type=PriceType.BID, start_time=START + minute*MINUTE,
        end_time=START + (minute+1)*MINUTE, available_at=START + (minute+1)*MINUTE,
        open=Decimal("1.1"), high=Decimal("1.3"), low=Decimal("1.0"), close=Decimal("1.2"))
    return MarketBar(**(fields | changes))

def request(instrument, **changes):
    return ResampleRequest(**(dict(instrument=instrument, timeframe=Timeframe.M1,
        price_type=PriceType.BID, start_time=START, end_time=START+2*MINUTE,
        missing_policy=MissingDataPolicy.OMIT) | changes))

def metadata(instrument, **changes):
    return DatasetMetadata(**(dict(instrument=instrument, description="Synthetic offline fixture",
        licensing="Synthetic; no provider data", transformation="identity-v1",
        source_id="fixture", observation_type=ObservationType.QUOTE) | changes))

def codes(report):
    return {i.code for i in report.issues}

def test_quality_does_not_clean_and_reports_order_duplicates(instrument):
    items = [quote(2), quote(1), quote(1)]
    report = validate_dataset(items, instrument=instrument)
    assert codes(report) == {"ordering", "duplicate", "exact_duplicate"}
    assert report.record_count == 3 and not report.gaps_checked
    assert items == [quote(2), quote(1), quote(1)]

def test_explicit_schedule_detects_edge_and_internal_gaps(instrument):
    report = validate_dataset([bar(1)], instrument=instrument,
        expected_starts=[START, START+MINUTE, START+2*MINUTE])
    assert report.gaps_checked and codes(report) == {"gap"}
    assert len(report.issues) == 2
    assert validate_dataset([], instrument=instrument).valid

def test_quality_checks_model_bypasses_and_series(instrument):
    broken = bar().model_copy(update={"high": Decimal("0.5")})
    assert codes(validate_dataset([broken], instrument=instrument)) == {"invalid_record"}
    assert "invalid_record" in codes(validate_dataset([quote().model_copy(update={"timestamp": START.replace(tzinfo=None)})], instrument=instrument))
    assert "instrument" in codes(validate_dataset([quote(instrument_id="OTHER")], instrument=instrument))
    assert "mixed_kind" in codes(validate_dataset([quote(), bar()], instrument=instrument))
    assert "mixed_series" in codes(validate_dataset([bar(), bar(1, price_type=PriceType.ASK)], instrument=instrument))
    assert "overlap" in codes(validate_dataset([bar(end_time=START+2*MINUTE, available_at=START+2*MINUTE), bar(1)], instrument=instrument))

@pytest.mark.parametrize("basis,expected", [(PriceType.BID,"1.1"),(PriceType.ASK,"1.2"),(PriceType.MID,"1.15")])
def test_quote_price_bases_missing_volume_and_availability(instrument, basis, expected):
    result = resample([quote(0), quote(30, available_at=START+3*MINUTE), quote(60)], request(instrument, price_type=basis))
    assert [r.start_time for r in result] == [START, START+MINUTE]
    assert result[0].open == Decimal(expected)
    assert result[0].available_at == START+3*MINUTE
    assert result[1].available_at == START+2*MINUTE
    assert result[0].volume is None and result[0].volume_type is None

def test_hand_computed_ohlc_and_low_decimal_precision(instrument):
    with localcontext() as context:
        context.prec = 2
        result = resample([quote(0,"1.10001","1.10003"), quote(10,"1.2","1.3"), quote(20,"1.0","1.1"), quote(30,"1.15","1.25")], request(instrument, price_type=PriceType.MID))
    assert (result[0].open,result[0].high,result[0].low,result[0].close) == tuple(map(Decimal,["1.10002","1.25","1.05","1.20"]))

def test_no_filling_and_reject_policy(instrument):
    assert len(resample([quote(60)],request(instrument))) == 1
    assert resample([],request(instrument)) == ()
    with pytest.raises(ValueError, match="missing"):
        resample([quote(60)], request(instrument,missing_policy=MissingDataPolicy.REJECT))

def test_partial_windows_and_trade_quotes_rejected(instrument):
    with pytest.raises(ValidationError, match="whole aligned"):
        request(instrument,start_time=START+timedelta(seconds=1))
    with pytest.raises(ValidationError):
        request(instrument,missing_policy="omit")
    with pytest.raises(ValueError, match="trade"):
        resample([quote()],request(instrument,price_type=PriceType.TRADE))
    with pytest.raises(ValueError, match="outside"):
        resample([quote(120)],request(instrument))

@pytest.mark.parametrize("records", [[quote(),quote()], [quote(2),quote(1)], [quote(),quote(1,source_id="other")]])
def test_ambiguous_inputs_rejected(instrument, records):
    with pytest.raises(ValueError):
        resample(records,request(instrument))

def test_bar_coarsening_preserves_extremes_volume_and_delays(instrument):
    records = [bar(i,volume=Decimal(i),volume_type=VolumeType.TICK_COUNT) for i in range(5)]
    records[-1] = bar(4,high=Decimal("1.5"),low=Decimal("0.9"),close=Decimal("1.4"),volume=Decimal(4),volume_type=VolumeType.TICK_COUNT,available_at=START+8*MINUTE)
    result = resample(records,request(instrument,timeframe=Timeframe.M5,end_time=START+5*MINUTE))
    assert len(result)==1
    assert (result[0].high,result[0].low,result[0].close,result[0].volume)==tuple(map(Decimal,["1.5","0.9","1.4","10"]))
    assert result[0].available_at == START+8*MINUTE

def test_incomplete_bars_not_bridged_and_missing_volume_stays_missing(instrument):
    req = request(instrument,timeframe=Timeframe.M5,end_time=START+5*MINUTE)
    assert resample([bar(0),bar(2),bar(4)],req)==()
    with pytest.raises(ValueError,match="incomplete"):
        resample([bar(0),bar(2),bar(4)],request(instrument,timeframe=Timeframe.M5,end_time=START+5*MINUTE,missing_policy=MissingDataPolicy.REJECT))
    records = [bar(i,volume=Decimal(1),volume_type=VolumeType.BASE) for i in range(5)]
    records[2] = bar(2)
    assert resample(records,req)[0].volume is None
    records[2] = bar(2,volume=Decimal(1),volume_type=VolumeType.QUOTE)
    with pytest.raises(ValueError,match="volume units"):
        resample(records,req)

def test_no_splitting_or_upsampling(instrument):
    with pytest.raises(ValueError,match="coarsening"):
        resample([bar(timeframe=Timeframe.M5,end_time=START+5*MINUTE,available_at=START+5*MINUTE)],request(instrument))
    with pytest.raises(ValueError,match="fixed UTC"):
        resample([bar(end_time=START+2*MINUTE,available_at=START+2*MINUTE)],request(instrument))
    with pytest.raises(ValueError,match="fixed UTC"):
        resample([bar(start_time=START+timedelta(seconds=30),end_time=START+timedelta(seconds=90),available_at=START+timedelta(seconds=90))],request(instrument))
    with pytest.raises(ValueError,match="fixed UTC"):
        resample([bar(end_time=START+2*MINUTE,available_at=START+2*MINUTE)],request(instrument))

def test_resampling_id_is_deterministic_and_input_sensitive(instrument):
    req=request(instrument)
    a=resample([quote()],req)[0]
    assert a == resample([quote()],req)[0]
    assert a.dataset_id != resample([quote(dataset_id="different-raw")],req)[0].dataset_id
    assert a.dataset_id != resample([quote()],request(instrument,price_type=PriceType.ASK))[0].dataset_id

@pytest.mark.parametrize("records", [[quote(),quote(1)], [bar(),bar(1)], []])
def test_storage_roundtrip_idempotence_and_metadata(tmp_path,instrument,records):
    store=SQLiteDatasetStore(tmp_path/"market.sqlite")
    info=metadata(instrument, observation_type=ObservationType.BAR if records and isinstance(records[0], MarketBar) else ObservationType.QUOTE)
    identity=store.save(records,info)
    assert identity==store.save(records,info)
    loaded=store.load(identity)
    assert loaded.observations==tuple(records)
    assert loaded.metadata.instrument==info.instrument
    assert loaded.metadata.dataset_id==identity
    assert loaded.metadata.record_count==len(records)
    assert loaded.metadata.created_at.tzinfo is timezone.utc
    assert loaded.metadata==store.load(identity).metadata
    assert loaded.quality.valid
    assert store.save(records,metadata(instrument, observation_type=info.observation_type, transformation="identity-v2"))!=identity

def test_storage_rejects_invalid_quality_and_preserves_provenance(tmp_path,instrument):
    store=SQLiteDatasetStore(tmp_path/"market.sqlite")
    with pytest.raises(ValueError,match="invalid research dataset"):
        store.save([quote(2),quote(1),quote(1)],metadata(instrument))
    assert not store.path.exists()
    identity=store.save([quote(1),quote(2)],metadata(instrument))
    assert store.load(identity).observations[0].dataset_id=="raw-v1"


def test_storage_detects_tampering(tmp_path,instrument):
    file=tmp_path/"market.sqlite"
    store=SQLiteDatasetStore(file)
    identity=store.save([quote()],metadata(instrument))
    with sqlite3.connect(file) as connection:
        connection.execute("UPDATE observations SET bid='1.15'")
    with pytest.raises(StorageIntegrityError):
        store.load(identity)
    with pytest.raises(StorageIntegrityError):
        store.save([quote()],metadata(instrument))

def test_storage_unknown_and_invalid_ids_are_read_only(tmp_path):
    file=tmp_path/"absent.sqlite"
    store=SQLiteDatasetStore(file)
    with pytest.raises(ValueError):
        store.load("../../escape")
    with pytest.raises(KeyError):
        store.load("sha256:"+"0"*64)
    assert not file.exists()

def test_storage_rejects_mixed_instruments_and_rolls_back(tmp_path,instrument):
    store=SQLiteDatasetStore(tmp_path/"market.sqlite")
    with pytest.raises(ValueError):
        store.save([quote(instrument_id="other")],metadata(instrument))
    assert not store.path.exists()
    with pytest.raises(ValueError):
        store.save([quote(),bar()],metadata(instrument))

@pytest.mark.parametrize("timeframe,seconds", [(Timeframe.M1,60),(Timeframe.M5,300),(Timeframe.M15,900),(Timeframe.M30,1800),(Timeframe.H1,3600),(Timeframe.H4,14400)])
def test_fixed_utc_intraday_timeframes(instrument,timeframe,seconds):
    result=resample([quote()],request(instrument,timeframe=timeframe,end_time=START+timedelta(seconds=seconds)))
    assert result[0].end_time==START+timedelta(seconds=seconds)

def test_input_after_bin_close_cannot_change_earlier_ohlc(instrument):
    req=request(instrument)
    first=resample([quote(0),quote(30)],req)[0]
    extended=resample([quote(0),quote(30),quote(60,"9","10")],req)[0]
    assert (first.open,first.high,first.low,first.close,first.available_at)==(extended.open,extended.high,extended.low,extended.close,extended.available_at)

def test_utc_offset_request_and_empty_scheduled_dataset(tmp_path,instrument):
    offset=timezone(timedelta(hours=5,minutes=30))
    req=request(instrument,start_time=START.astimezone(offset),end_time=(START+2*MINUTE).astimezone(offset))
    assert resample([quote()],req)[0].start_time.tzinfo is timezone.utc
    store=SQLiteDatasetStore(tmp_path/"market.sqlite")
    with pytest.raises(ValueError,match="invalid research dataset"):
        store.save([],metadata(instrument,expected_starts=(START,START+MINUTE)))

def test_schema_version_and_count_corruption(tmp_path,instrument):
    file=tmp_path/"market.sqlite"
    store=SQLiteDatasetStore(file)
    identity=store.save([bar()],metadata(instrument, observation_type=ObservationType.BAR))
    with sqlite3.connect(file) as connection:
        connection.execute("UPDATE datasets SET record_count=2")
    with pytest.raises(StorageIntegrityError):
        store.load(identity)
    with sqlite3.connect(file) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(StorageIntegrityError,match="schema version"):
        store.load(identity)
    with pytest.raises(StorageIntegrityError,match="schema version"):
        store.save([bar()],metadata(instrument,observation_type=ObservationType.BAR))

def test_transaction_rolls_back_partial_dataset(tmp_path,instrument):
    file=tmp_path/"market.sqlite"
    store=SQLiteDatasetStore(file)
    initial=store.save([quote()],metadata(instrument))
    with sqlite3.connect(file) as connection:
        connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON observations WHEN NEW.position=1 BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.save([quote(),quote(1)],metadata(instrument))
    with sqlite3.connect(file) as connection:
        assert connection.execute("SELECT COUNT(*) FROM datasets").fetchone()[0]==1
        assert connection.execute("SELECT COUNT(*) FROM observations").fetchone()[0]==1
    assert store.load(initial).observations==(quote(),)

def test_offline_ingestion_to_validated_stored_bars(tmp_path,instrument):
    import lzma
    import struct
    from quantlab.data.providers import HistoricalQuoteRequest
    from quantlab.data.providers.dukascopy import DukascopyProvider
    payload=lzma.compress(struct.pack(">IIIff",0,110002,110000,1.0,1.0)+struct.pack(">IIIff",60000,110004,110002,1.0,1.0),format=lzma.FORMAT_ALONE)
    provider=DukascopyProvider(fetcher=lambda *args,**kwargs: payload)
    raw=tuple(provider.fetch(HistoricalQuoteRequest(instrument=instrument,start_time=START,end_time=START+2*MINUTE)))
    assert validate_dataset(raw,instrument=instrument).valid
    store=SQLiteDatasetStore(tmp_path/"market.sqlite")
    parent=store.save(raw,metadata(instrument,source_id="dukascopy"))
    bars=resample(raw,request(instrument))
    child=store.save(bars,metadata(instrument,source_id="dukascopy",observation_type=ObservationType.BAR,parent_ids=(parent,),transformation="utc-ohlc",transformation_version="2"))
    loaded=store.load(child)
    assert loaded.observations==bars and loaded.metadata.parent_ids==(parent,)
    assert loaded.observations[0].close==Decimal("1.10000")


def test_corrupted_instrument_is_a_storage_integrity_error(tmp_path,instrument):
    file=tmp_path/"market.sqlite"
    store=SQLiteDatasetStore(file)
    identity=store.save([quote()],metadata(instrument))
    with sqlite3.connect(file) as connection:
        connection.execute("UPDATE observations SET instrument_id='other'")
    with pytest.raises(StorageIntegrityError,match="invalid stored dataset"):
        store.load(identity)


def test_clean_typed_quality_summary(instrument):
    report = validate_dataset([quote(), quote(1), quote(10)], instrument=instrument)
    assert isinstance(report, DataQualityReport)
    assert report.valid and report.observation_count == 3
    assert report.duplicate_count == report.duplicate_timestamp_count == 0
    assert report.out_of_order_count == report.non_monotonic_count == report.gap_count == 0
    assert report.first_timestamp == START
    assert report.last_timestamp == START + timedelta(seconds=10)
    assert report.errors == report.warnings == ()
    assert DataQualityReport.model_validate_json(report.model_dump_json()) == report
    with pytest.raises(ValidationError, match="frozen"):
        report.observation_count = 4


def test_exact_duplicate_counts_are_distinct_from_timestamp_counts(instrument):
    a = quote()
    b = quote(bid="1.11")
    report = validate_dataset([a, b, a], instrument=instrument)
    assert report.duplicate_count == 1
    assert report.duplicate_timestamp_count == 2
    assert report.non_monotonic_count == 2
    assert report.out_of_order_count == 0
    assert not report.valid


def test_ordering_count_and_chronological_bounds(instrument):
    records = [quote(10), quote(1), quote(5)]
    original = tuple(records)
    report = validate_dataset(iter(records), instrument=instrument)
    assert report.out_of_order_count == report.non_monotonic_count == 1
    assert report.first_timestamp == START + timedelta(seconds=1)
    assert report.last_timestamp == START + timedelta(seconds=10)
    assert tuple(records) == original


@pytest.mark.parametrize("threshold,expected_gaps", [(None, 0), (10, 1), (20, 0)])
def test_irregular_quotes_use_only_explicit_gap_threshold(instrument, threshold, expected_gaps):
    options = ValidationOptions(max_gap=None if threshold is None else timedelta(seconds=threshold))
    report = validate_dataset([quote(), quote(10), quote(30)], instrument=instrument, options=options)
    assert report.gap_count == expected_gaps
    assert report.gaps_checked == (threshold is not None)
    assert report.valid == (expected_gaps == 0)


@pytest.mark.parametrize("threshold", [timedelta(0), timedelta(seconds=-1)])
def test_gap_threshold_must_be_positive(threshold):
    with pytest.raises(ValidationError, match="positive"):
        ValidationOptions(max_gap=threshold)


def test_empty_report_is_explicit_without_invented_coverage(instrument):
    report = validate_dataset([], instrument=instrument)
    assert report.valid and report.observation_count == 0
    assert [issue.code for issue in report.warnings] == ["empty"]
    assert report.first_timestamp is report.last_timestamp is None
    assert not report.gaps_checked


def test_source_and_dataset_homogeneity_are_configurable(instrument):
    records = [quote(), quote(1, source_id="other", dataset_id="raw-v2")]
    assert "source" in codes(validate_dataset(records, instrument=instrument))
    assert validate_dataset(records, instrument=instrument,
        options=ValidationOptions(homogeneous_source=False)).valid
    report = validate_dataset(records, instrument=instrument,
        options=ValidationOptions(homogeneous_dataset=True))
    assert codes(report) == {"source", "dataset"}
    assert "dataset" in codes(validate_dataset([quote()], instrument=instrument,
        options=ValidationOptions(expected_dataset_id="different")))
    assert "source" in codes(validate_dataset([quote()], instrument=instrument,
        options=ValidationOptions(expected_source_id="different")))


def test_hourly_provider_dataset_ids_are_not_assumed_homogeneous(instrument):
    records = [quote(), quote(1, dataset_id="second-source-file")]
    assert validate_dataset(records, instrument=instrument).valid
    assert not validate_dataset(records, instrument=instrument,
        options=ValidationOptions(homogeneous_dataset=True)).valid


@pytest.mark.parametrize("policy,expected_count", [(DuplicatePolicy.KEEP, 3), (DuplicatePolicy.REMOVE, 2)])
def test_explicit_normalization_is_stable_and_immutable(policy, expected_count):
    records = [quote(2), quote(1), quote(1)]
    original = tuple(records)
    normalized = normalize_observations(records, duplicate_policy=policy)
    assert isinstance(normalized, tuple) and len(normalized) == expected_count
    assert list(normalized) == sorted(normalized, key=lambda q: q.timestamp)
    assert tuple(records) == original


def test_normalization_default_rejects_exact_duplicates():
    with pytest.raises(ValueError, match="exact duplicate"):
        normalize_observations([quote(), quote()])
    with pytest.raises(ValueError, match="DuplicatePolicy"):
        normalize_observations([quote()], duplicate_policy="remove")


def test_normalization_preserves_distinct_quotes_at_same_timestamp():
    a, b = quote(bid="1.11"), quote(bid="1.12")
    assert normalize_observations([quote(2), a, b], duplicate_policy=DuplicatePolicy.REMOVE) == (a, b, quote(2))


def test_resampling_accepts_explicit_normalization(instrument):
    raw = [quote(30, "1.2", "1.3"), quote(), quote()]
    with pytest.raises(ValueError, match="invalid input"):
        resample(raw, request(instrument))
    normalized = normalize_observations(raw, duplicate_policy=DuplicatePolicy.REMOVE)
    result = resample(normalized, request(instrument))
    assert result[0].open == Decimal("1.1")
    assert result[0].close == Decimal("1.2")
    assert result[0].volume is result[0].volume_type is None


@pytest.mark.parametrize("timeframe,minutes", [(Timeframe.M5, 5), (Timeframe.H1, 60), (Timeframe.H4, 240)])
def test_non_midnight_interval_alignment(instrument, timeframe, minutes):
    start = START + timedelta(hours=12)
    records = [quote(12*3600 + 1), quote(12*3600 + minutes*60, "1.3", "1.4")]
    result = resample(records, request(instrument, timeframe=timeframe,
        start_time=start, end_time=start + timedelta(minutes=2*minutes)))
    assert [b.start_time for b in result] == [start, start+timedelta(minutes=minutes)]
    assert result[0].close == Decimal("1.1")
    assert result[1].open == Decimal("1.3")
    assert all(b.volume is None and b.volume_type is None for b in result)


def test_metadata_is_complete_and_can_be_saved_loaded_losslessly(tmp_path, instrument):
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    records = resample([quote(), quote(60)], request(instrument))
    identity = store.save(records, metadata(instrument, observation_type=ObservationType.BAR,
        transformation="utc-ohlc", transformation_version="2", parent_ids=("raw-v1",)))
    loaded = store.load(identity)
    info = loaded.metadata
    assert info.dataset_id == identity and info.source_id == "fixture"
    assert info.instrument_id == instrument.instrument_id
    assert info.observation_type is ObservationType.BAR
    assert info.timeframe is Timeframe.M1 and info.price_type is PriceType.BID
    assert info.start_time == START and info.end_time == START + 2*MINUTE
    assert info.record_count == 2 and info.parent_ids == ("raw-v1",)
    assert info.transformation_version == "2"
    assert info.created_at.tzinfo is timezone.utc
    assert loaded.observations == records
    assert store.save(records, info) == identity
    assert store.load(identity).metadata == info


def test_creation_time_does_not_change_identity_or_overwrite_existing_metadata(tmp_path, instrument):
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    initial = metadata(instrument, created_at=START)
    identity = store.save([quote()], initial)
    with sqlite3.connect(store.path) as connection:
        original = connection.execute("SELECT metadata FROM datasets").fetchone()[0]
    assert store.save([quote()], metadata(instrument, created_at=START+MINUTE)) == identity
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT metadata FROM datasets").fetchone()[0] == original
    assert store.load(identity).metadata.created_at == START
    assert store.save([quote()], metadata(instrument, transformation_version="2")) != identity


@pytest.mark.parametrize("changes", [{"record_count": 10}, {"source_id": "wrong"},
    {"instrument_id": "wrong"}, {"timeframe": Timeframe.M1},
    {"dataset_id": "sha256:" + "0"*64}, {"start_time": START-MINUTE, "end_time": START}])
def test_inconsistent_declared_metadata_is_rejected_before_writing(tmp_path, instrument, changes):
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    with pytest.raises(ValueError, match="disagrees"):
        store.save([quote()], metadata(instrument, **changes))
    assert not store.path.exists()


def test_storage_enforces_declared_gap_threshold(tmp_path, instrument):
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    with pytest.raises(ValueError, match="invalid research dataset"):
        store.save([quote(), quote(120)], metadata(instrument,
            validation_options=ValidationOptions(max_gap=timedelta(seconds=30))))
    assert not store.path.exists()


@pytest.mark.parametrize("column,value", [("bid", "not-a-decimal"), ("timestamp", "2024-01-01T00:00:00"),
    ("timeframe", "invalid-enum"), ("bid", "9.9")])
def test_malformed_stored_records_are_rejected(tmp_path, instrument, column, value):
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    identity = store.save([quote()], metadata(instrument))
    with sqlite3.connect(store.path) as connection:
        # column is a fixed test parameter, never untrusted user input.
        connection.execute('UPDATE observations SET "' + column + '"=?', (value,))
    with pytest.raises(StorageIntegrityError):
        store.load(identity)


def test_inconsistent_metadata_and_report_corruption_are_rejected(tmp_path, instrument):
    import json
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    identity = store.save([quote()], metadata(instrument))
    with sqlite3.connect(store.path) as connection:
        text = connection.execute("SELECT quality FROM datasets").fetchone()[0]
        altered = json.loads(text)
        altered["observation_count"] = 100
        connection.execute("UPDATE datasets SET quality=?", (json.dumps(altered),))
    with pytest.raises(StorageIntegrityError):
        store.load(identity)


def test_generic_phase4_operations_make_no_network_calls(tmp_path, instrument, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("generic Phase 4 operations must not call HTTP")
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    raw = normalize_observations([quote(60), quote()])
    assert validate_dataset(raw, instrument=instrument).valid
    bars = resample(raw, request(instrument))
    store = SQLiteDatasetStore(tmp_path / "research.sqlite")
    identity = store.save(bars, metadata(instrument, observation_type=ObservationType.BAR))
    assert store.load(identity).observations == bars


@pytest.mark.parametrize("timeframe", [Timeframe.D1, Timeframe.W1])
def test_daily_and_weekly_resampling_targets_are_rejected(instrument, timeframe):
    with pytest.raises(ValidationError, match="daily and weekly bars.*market/session/calendar"):
        request(instrument, timeframe=timeframe)


@pytest.mark.parametrize("timeframe", [Timeframe.D1, Timeframe.W1])
def test_resampling_revalidates_unsupported_target_on_unchecked_copy(instrument, timeframe):
    unsupported = request(instrument).model_copy(update={"timeframe": timeframe})
    with pytest.raises(ValidationError, match="supported targets are M1, M5, M15, M30, H1, H4"):
        resample([quote()], unsupported)


@pytest.mark.parametrize("timeframe", [Timeframe.D1, Timeframe.W1])
def test_daily_and_weekly_input_bars_fail_clearly(instrument, timeframe):
    observation = bar(timeframe=timeframe)
    with pytest.raises(ValueError, match="daily and weekly input bars.*market/session/calendar"):
        resample([observation], request(instrument))
