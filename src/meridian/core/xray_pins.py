"""The shared Xray archive identity, including the geodata used by client policies."""

XRAY_VERSION = "26.7.28"
XRAY_GITHUB_URL = "https://github.com/XTLS/Xray-core/releases/download"
XRAY_ASSET_MAP: dict[tuple[str, str], str] = {
    ("Darwin", "arm64"): "Xray-macos-arm64-v8a.zip",
    ("Darwin", "x86_64"): "Xray-macos-64.zip",
    ("Linux", "x86_64"): "Xray-linux-64.zip",
    ("Linux", "aarch64"): "Xray-linux-arm64-v8a.zip",
}
XRAY_SHA256: dict[str, str] = {
    "Xray-macos-arm64-v8a.zip": "9b99a351febe31b7e0c7f22deeb1577a1da0b98aaa51aec7fd17832e68cf63d6",
    "Xray-macos-64.zip": "812f7d9de6d3506795eabda2f6928ba301c632c3fe6fa39c52ea8e0ed9e4e244",
    "Xray-linux-64.zip": "8195d909f1109b8f3d99eefe401a3c451d7bf4af71f24d3815420f77e5dd2a40",
    "Xray-linux-arm64-v8a.zip": "f5698bb218ada3b4022db26fafc39601c5f53b46b19eb76c9616325985807501",
}
XRAY_ARCHIVE_BYTES: dict[str, int] = {
    "Xray-linux-64.zip": 21164807,
    "Xray-linux-arm64-v8a.zip": 19699639,
}
