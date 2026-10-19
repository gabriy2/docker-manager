from datetime import UTC

from app.docker_service import _parse_docker_time, _ports, parse_stats


def test_parse_docker_time_con_nanosecondi():
    parsed = _parse_docker_time("2026-10-19T20:15:30.123456789Z")
    assert parsed is not None
    assert parsed.tzinfo == UTC
    assert parsed.microsecond == 123456


def test_parse_docker_time_valore_nullo():
    assert _parse_docker_time("0001-01-01T00:00:00Z") is None
    assert _parse_docker_time(None) is None
    assert _parse_docker_time("non-una-data") is None


def test_parse_stats_calcola_cpu_e_memoria():
    raw = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 2_000_000_000},
            "system_cpu_usage": 20_000_000_000,
            "online_cpus": 4,
        },
        "precpu_stats": {
            "cpu_usage": {"total_usage": 1_000_000_000},
            "system_cpu_usage": 10_000_000_000,
        },
        "memory_stats": {"usage": 600 * 1024**2, "limit": 1024**3, "stats": {"inactive_file": 100 * 1024**2}},
        "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 500}},
    }
    stats = parse_stats(raw)
    assert stats["cpu_percent"] == 40.0
    assert stats["mem_usage"] == 500 * 1024**2
    assert round(stats["mem_percent"]) == 49
    assert stats["net_rx"] == 1000


def test_parse_stats_tollera_dati_mancanti():
    stats = parse_stats({})
    assert stats["cpu_percent"] == 0.0
    assert stats["mem_usage"] == 0


def test_ports_costruisce_url():
    attrs = {"NetworkSettings": {"Ports": {
        "80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
        "443/tcp": None,
    }}}
    ports = _ports(attrs)
    assert {"container": "443/tcp", "host": None, "url": None} in ports
    published = next(p for p in ports if p["container"] == "80/tcp")
    assert published["url"] == "http://localhost:8080"
