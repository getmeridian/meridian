"""Tests for the typed nginx configuration model.

Verifies that the model-based rendering produces output identical to the
original string-based generation, and tests the model's data construction.
"""

from __future__ import annotations

from meridian.provision.nginx_model import (
    NginxStreamBlock,
    NginxStreamInclude,
    NginxStreamMap,
    NginxUpstream,
    build_stream_block,
)


# ---------------------------------------------------------------------------
# NginxStreamBlock.render() — output fidelity
# ---------------------------------------------------------------------------


class TestStreamBlockRender:
    """Verify that model rendering produces valid nginx stream config."""

    def test_render_has_ssl_preread(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        cfg = block.render()
        assert "ssl_preread on" in cfg

    def test_render_has_map_hash_bucket_size(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        cfg = block.render()
        assert "map_hash_bucket_size 128;" in cfg

    def test_render_has_proxy_pass(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        cfg = block.render()
        assert "proxy_pass $meridian_backend" in cfg

    def test_render_has_relay_maps_include(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        cfg = block.render()
        assert "include /etc/nginx/stream.d/relay-maps/*.conf;" in cfg

    def test_render_has_all_upstreams(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        cfg = block.render()
        assert "upstream xray_reality" in cfg
        assert "upstream nginx_https" in cfg
        assert "upstream reality_dest" in cfg

    def test_render_listen_port(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        cfg = block.render()
        assert "listen 443;" in cfg


# ---------------------------------------------------------------------------
# build_stream_block() — data construction
# ---------------------------------------------------------------------------


class TestBuildStreamBlock:
    def test_ip_only_has_server_ip_map(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        sni_values = [m.sni for m in block.maps]
        assert "198.51.100.1" in sni_values
        assert "www.microsoft.com" in sni_values

    def test_domain_adds_domain_map(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
            domain="example.com",
        )
        sni_values = [m.sni for m in block.maps]
        assert "example.com" in sni_values

    def test_no_domain_no_domain_map(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        sni_values = [m.sni for m in block.maps]
        assert "example.com" not in sni_values

    def test_empty_sni_always_present(self):
        """No-SNI (bare IP access) map entry is always included."""
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        sni_values = [m.sni for m in block.maps]
        assert '""' in sni_values

    def test_default_upstream_is_reality_dest(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        assert block.default_upstream == "reality_dest"

    def test_upstream_servers_match_ports(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=12345,
            nginx_internal_port=9999,
        )
        upstream_servers = {u.name: u.server for u in block.upstreams}
        assert upstream_servers["xray_reality"] == "127.0.0.1:12345"
        assert upstream_servers["nginx_https"] == "127.0.0.1:9999"
        assert upstream_servers["reality_dest"] == "www.microsoft.com:443"

    def test_flow_comments_include_reality_sni(self):
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        assert any("www.microsoft.com" in fc for fc in block.flow_comments)

    def test_flow_comments_count_with_domain(self):
        """With IP and domain, 5 flow comments: reality, IP, domain, no-SNI, unknown."""
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
            domain="example.com",
        )
        assert len(block.flow_comments) == 5

    def test_flow_comments_count_ip_only(self):
        """With IP only, 4 flow comments: reality, IP, no-SNI, unknown."""
        block = build_stream_block(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert len(block.flow_comments) == 4


# ---------------------------------------------------------------------------
# Model-vs-original output equivalence
# ---------------------------------------------------------------------------


class TestStreamModelEquivalence:
    """Ensure model-based rendering is byte-identical to the original."""

    def _original_render(
        self,
        reality_sni: str,
        reality_backend_port: int,
        nginx_internal_port: int,
        server_ip: str = "",
        domain: str = "",
    ) -> str:
        """Call the migrated _render_nginx_stream_config (now model-based)."""
        from meridian.provision.services import _render_nginx_stream_config

        return _render_nginx_stream_config(
            reality_sni=reality_sni,
            reality_backend_port=reality_backend_port,
            nginx_internal_port=nginx_internal_port,
            server_ip=server_ip,
            domain=domain,
        )

    def _model_render(
        self,
        reality_sni: str,
        reality_backend_port: int,
        nginx_internal_port: int,
        server_ip: str = "",
        domain: str = "",
    ) -> str:
        block = build_stream_block(
            reality_sni=reality_sni,
            reality_backend_port=reality_backend_port,
            nginx_internal_port=nginx_internal_port,
            server_ip=server_ip,
            domain=domain,
        )
        return block.render()

    def test_ip_only_identical(self):
        args = dict(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
        )
        assert self._original_render(**args) == self._model_render(**args)

    def test_with_domain_identical(self):
        args = dict(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
            server_ip="198.51.100.1",
            domain="example.com",
        )
        assert self._original_render(**args) == self._model_render(**args)

    def test_no_server_ip_identical(self):
        args = dict(
            reality_sni="www.microsoft.com",
            reality_backend_port=10443,
            nginx_internal_port=8443,
        )
        assert self._original_render(**args) == self._model_render(**args)

    def test_different_ports_identical(self):
        args = dict(
            reality_sni="cdn.example.net",
            reality_backend_port=20443,
            nginx_internal_port=9443,
            server_ip="198.51.100.42",
            domain="vpn.example.org",
        )
        assert self._original_render(**args) == self._model_render(**args)


# ---------------------------------------------------------------------------
# NginxStreamBlock direct construction (not via builder)
# ---------------------------------------------------------------------------


class TestNginxStreamBlockDirect:
    """Test the dataclass used directly for custom stream configs."""

    def test_empty_block_renders(self):
        block = NginxStreamBlock()
        cfg = block.render()
        assert "listen 443;" in cfg
        assert "ssl_preread on;" in cfg

    def test_custom_listen_port(self):
        block = NginxStreamBlock(listen_port=8443)
        cfg = block.render()
        assert "listen 8443;" in cfg

    def test_custom_proxy_timeout(self):
        block = NginxStreamBlock(proxy_timeout="1h")
        cfg = block.render()
        assert "proxy_timeout 1h;" in cfg

    def test_no_keepalive(self):
        block = NginxStreamBlock(proxy_socket_keepalive=False)
        cfg = block.render()
        assert "proxy_socket_keepalive" not in cfg

    def test_custom_map_hash_bucket_size(self):
        block = NginxStreamBlock(map_hash_bucket_size=256)
        cfg = block.render()
        assert "map_hash_bucket_size 256;" in cfg
