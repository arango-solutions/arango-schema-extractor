from schema_analyzer.utils import analysis_cache_storage_key, extract_first_json_object


def test_extract_first_json_object_simple():
    txt = '{"a": 1}'
    assert extract_first_json_object(txt) == txt


def test_extract_first_json_object_with_preamble():
    txt = 'hello\n\n{ "a": {"b": 2}, "c": 3 }\nbye'
    assert extract_first_json_object(txt).strip().startswith("{")
    assert extract_first_json_object(txt).strip().endswith("}")


def test_extract_first_json_object_handles_strings():
    txt = 'preamble {"a": "x}y", "b": 1} post'
    assert extract_first_json_object(txt) == '{"a": "x}y", "b": 1}'


def test_analysis_cache_storage_key_baseline_unchanged():
    fp = "deadbeef" * 8
    assert analysis_cache_storage_key(fp, llm_cache_segment=None) == fp


def test_analysis_cache_storage_key_llm_segment_changes_key():
    fp = "abc"
    k1 = analysis_cache_storage_key(fp, llm_cache_segment="v1\x00sysA")
    k2 = analysis_cache_storage_key(fp, llm_cache_segment="v1\x00sysB")
    assert k1 != k2
    assert len(k1) == 64
