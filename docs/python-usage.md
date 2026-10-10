# Pythonライブラリの利用例

各ベンダーのコマンド出力を共通モデルへ変換し、同じコードで参照する例です。リポジトリ直下でインストールして実行してください。入力は合成サンプルで、実機検証を示すものではありません。

## 複数ベンダーの経路を同じ形式で参照する

```python
from pathlib import Path

from nwconfig_parser.cli import build_registry
from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.models import RoutingTable
from nwconfig_parser.parsers.base import ParseContext

engine = ParserEngine(build_registry())
samples = [
    ("Yamaha", "RTX", "show ip route", "yamaha_rtx_show_ip_route.txt"),
    (
        "Fortinet",
        "FortiOS",
        "get router info routing-table all",
        "fortinet_fortios_routing_table_all.txt",
    ),
    ("A10", "ACOS", "show ip route", "a10_acos_show_ip_route.txt"),
    (
        "HPE",
        "Comware",
        "display ip routing-table",
        "hpe_comware_display_ip_routing_table.txt",
    ),
]

for vendor, os_family, command, filename in samples:
    path = Path("tests/config") / filename
    result = engine.parse(
        path.read_text(encoding="utf-8"),
        ParseContext(
            vendor=vendor,
            os_family=os_family,
            command=command,
            filename=str(path),
        ),
    )
    print(vendor, result.status.value)
    if isinstance(result.data, RoutingTable):
        for route in result.data.routes:
            print(
                str(route.network),
                route.protocol,
                [str(ip) for ip in route.next_hops],
                route.outgoing_interfaces,
                route.vrf_scope.value,
                route.vrf,
                route.vrf_id,
            )
    for issue in result.issues:
        print(issue.code, issue.line_number)
```

この例は参照方法を共通化するもので、同じ宛先の経路を統合する処理ではありません。FortiOSの出力では複数VRFが単一の`RoutingTable`に含まれることがあり、個々の経路の`vrf_scope`・`vrf`・`vrf_id`を確認してください。CiscoのVRF経路パーサは`list[RoutingTable]`を返します。

`PARTIAL_SUCCESS`では上のループで取得済み経路を確認できますが、未知の行や所属VRFが不明な経路が残る可能性があります。経路が取得できないことだけで、機器に経路がないと判断しないでください。

## 設定値を見やすく表示する

`ConfigDocument`を取得した後、必要なInterfaceだけを検索して表示できます。

```python
from dataclasses import asdict
from pprint import pprint

from nwconfig_parser.models import ConfigDocument

if isinstance(result.data, ConfigDocument):
    target = next(
        (i for i in result.data.interfaces if i.name == "GigabitEthernet0/1"),
        None,
    )
    if target is not None:
        pprint(asdict(target), sort_dicts=False)
```

ここでの`result`は、READMEの設定解析例などで取得した結果です。運用コマンドのInterface一覧は`list[Interface]`なので、そのリストから検索します。設定全体の`raw_text`やノードは入力本文を保持するため、共有用の表示には必要な設定項目だけを選んでください。

共通モデルの定義は`src/nwconfig_parser/models.py`、コマンドごとの対応範囲は[サポート状況](support-status.md)を参照してください。
