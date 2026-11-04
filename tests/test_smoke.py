import flmp_core
import flmp_relay
import flmp_server


def test_packages_import() -> None:
    assert flmp_core.PROTOCOL_VERSION == 1
    assert flmp_relay.__doc__
    assert flmp_server.__doc__
