from crewquarters_contracts import loader


def test_contracts_dir_contains_the_contract_files() -> None:
    root = loader.contracts_dir()
    for name in (
        "agent-manifest.schema.json",
        "capabilities.yaml",
        "broker-sdk.openapi.yaml",
        "openapi.yaml",
        "events/run-event.schema.json",
    ):
        assert (root / name).is_file(), name


def test_loaders_return_parsed_documents() -> None:
    assert loader.manifest_schema()["$schema"].endswith("2020-12/schema")
    assert "input.ask" in loader.capabilities()["capabilities"]
    assert loader.broker_openapi()["openapi"].startswith("3.1")
    assert loader.control_openapi()["openapi"].startswith("3.1")
    assert loader.run_event_schema()["title"] == "RunEvent"
