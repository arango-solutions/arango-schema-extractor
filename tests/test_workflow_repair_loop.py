from schema_analyzer.workflow import run_generate_validate_repair


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate(self, *, model: str, system: str, prompt: str, timeout_ms: int):
        self.calls.append({"model": model, "system": system, "prompt": prompt, "timeout_ms": timeout_ms})
        text = self.outputs.pop(0)
        return type("Resp", (), {"text": text})()


def test_repair_loop_repairs_invalid_output_then_succeeds():
    invalid = '{"conceptualSchema":{}, "physicalMapping":{}, "metadata":{}}'
    valid = (
        "{"
        '"conceptualSchema":{"entities":[],"relationships":[],"properties":[]},'
        '"physicalMapping":{"entities":{},"relationships":{}},'
        '"metadata":{"confidence":0.5,"timestamp":"t","analyzedCollectionCounts":{"documentCollections":0,"edgeCollections":0},"detectedPatterns":[]}'
        "}"
    )
    provider = FakeProvider([invalid, valid])
    res = run_generate_validate_repair(
        provider=provider, model="m", system="s", prompt="p", timeout_ms=1000, max_repair_attempts=2
    )
    assert res.repair_attempts == 1
    assert res.data["conceptualSchema"]["entities"] == []
    assert len(provider.calls) == 2
