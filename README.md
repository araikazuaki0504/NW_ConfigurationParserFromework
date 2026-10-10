# NW Config Parser

各ベンダーのネットワーク機器から取得したコマンド出力を解析し、表記・構文・データ構造の違いを吸収して、共通の型付きデータモデルへ変換するPythonライブラリです。

Cisco、Yamaha、Fortinet、A10、HPE Comwareの対応コマンドについて、利用側がベンダーごとの文字列処理を書かずに経路やInterfaceの値を参照できることを目的としています。設定表示コマンドから取得したconfigも解析対象です。CLIによるJSON出力も提供します。

## 共通フォーマット

同じ種類の情報を、ベンダーに共通のモデルで表します。経路は`Route`、Interfaceは`Interface`、設定全体は`ConfigDocument`です。IPアドレスやネットワークは標準ライブラリの`ipaddress`型で保持します。

例えばCiscoの`show ip route`、Yamahaの`show ip route`、Fortinetの`get router info routing-table all`は、いずれも`RoutingTable.routes`から`Route.network`や`Route.next_hops`を参照できます。

すべてのコマンドが同じコンテナ型を返すわけではありません。共通フォーマットは、情報の種類ごとの共通モデルを意味します。

| 情報 | `ParseResult.data`の型 | 主な参照先 |
|---|---|---|
| 通常の経路表示 | `RoutingTable` | `.routes` |
| CiscoのVRF別経路表示 | `list[RoutingTable]` | 各テーブルの`.vrf`、`.routes` |
| Interface表示 | `list[Interface]` | `.name`、`.ipv4_address`、`.operational_status`など |
| VLAN表示 | `list[VLAN]` | `.vlan_id`、`.name`など |
| 設定表示 | `ConfigDocument` | `.interfaces`、`.static_routes`、`.vrfs`など |

設定のInterfaceアドレスは`ipv4_addresses`で保持します。運用コマンドでは出力形式に応じて`ipv4_address`または`ipv4_addresses`を使用します。取得できる項目はコマンドごとに異なります。

ベンダー固有情報は必要に応じて`attributes`に保持します。VRF名と数値ID、管理状態と稼働状態など、意味が異なる情報は区別します。未記載の値や機器のデフォルト値を推測して埋めません。

## 動作要件とインストール

Python 3.12以上が必要です。標準ライブラリのみで動作します。

```shell
python -m pip install -e .
```

## Pythonライブラリとして使う

登録済みパーサのRegistryでEngineを作り、取得済みテキストと`ParseContext`を渡します。`build_registry()`は現在`cli.py`で提供していますが、Pythonからも使用できます。

```python
from nwconfig_parser.cli import build_registry
from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.input import load_text_file
from nwconfig_parser.models import ParseStatus, RoutingTable
from nwconfig_parser.parsers.base import ParseContext

engine = ParserEngine(build_registry())
capture = load_text_file("tests/config/yamaha_rtx_show_ip_route.txt")
result = engine.parse(
    capture.raw_text,
    ParseContext(
        vendor="Yamaha",
        os_family="RTX",
        command="show ip route",
        filename="tests/config/yamaha_rtx_show_ip_route.txt",
    ),
)

print(result.status.value)
if result.status is not ParseStatus.FAILED:
    if isinstance(result.data, RoutingTable):
        for route in result.data.routes:
            print(str(route.network), [str(ip) for ip in route.next_hops])
```

別ベンダーでも、対応する経路コマンドと入力ファイルを指定すれば、同じ参照処理を使用できます。複数ベンダーの例は[Python利用ガイド](docs/python-usage.md)を参照してください。

`ParseContext`はパーサ選択と入力元の記録に使います。Engine経由では`vendor`、`os_family`、`command`が必須です。必要に応じて`os_version`や`source_metadata`も指定できます。未対応や曖昧な選択は`ParserSelectionError`のサブクラスで報告し、入力からOSを推測して別のパーサへ切り替えません。

## 設定から特定のInterfaceを取得する

上の例と同じEngineで、設定表示の結果も解析できます。

```python
from pathlib import Path
from nwconfig_parser.models import ConfigDocument

path = Path("tests/config/cisco_ios_xe_vrf_running_config.cfg")
result = engine.parse(
    path.read_text(encoding="utf-8"),
    ParseContext(
        vendor="Cisco", os_family="IOS XE",
        command="running-config", filename=str(path),
    ),
)

if isinstance(result.data, ConfigDocument):
    interfaces = {item.name: item for item in result.data.interfaces}
    interface = interfaces.get("GigabitEthernet0/1")
    if interface is not None:
        print(interface.vrf)
        print([str(address) for address in interface.ipv4_addresses])
```

設定パーサはベンダーを問わず`ConfigDocument`を返します。Interface名は入力の表記を保持するため、検索には解析結果の名前を使用してください。

## 現在の対応コマンド

以下はRegistryで使用するコマンド識別子です。Ciscoのconfig解析では`show running-config`ではなく`running-config`を指定します。

| ベンダー / OS | コマンド出力 | 設定表示 |
|---|---|---|
| Cisco / IOS・IOS XE | `show ip route`、`show ip route vrf <名前または*>`、`show interfaces status`、`show ip interface brief`、`show vlan brief` | `running-config` |
| Yamaha / RTX | `show ip route` | `show config` |
| Fortinet / FortiOS | `get router info routing-table all`、`get system interface` | `show` |
| A10 / ACOS | `show ip route`、`show interfaces` | `show running-config` |
| HPE / Comware | `display ip routing-table`、`display ip routing-table vpn-instance <名前>`、`display interface` | `display current-configuration` |

コマンドが登録されていても、その全構文・全出力形式に対応するわけではありません。詳細は[設定パーサの対応表](docs/vendor-config-support.md)、[サポート状況](docs/support-status.md)、[VRF対応表](docs/vrf-support.md)を参照してください。詳細の調査記録には英語の文書もあります。

Cisco NX-OS、ArubaOS-CX、ArubaOS-Switchのパーサは未登録です。IPv6とLLDPは対象外です。機器接続やコマンド実行は行わず、取得済みのテキストを入力にします。

## 解析結果と不完全な入力

各パーサは`ParseResult`を返します。

| 属性 | 内容 |
|---|---|
| `status` | `SUCCESS`、`PARTIAL_SUCCESS`、`FAILED` |
| `data` | 共通モデルに変換したデータ |
| `issues` | 問題のコード・重大度・位置など |
| `unparsed_ranges` | 未解析箇所の入力元情報 |
| `parser_metadata` | 使用したパーサの情報 |
| `source_metadata` | 入力元の追加情報 |

`PARTIAL_SUCCESS`でも取得済みデータは利用できますが、完全な解析結果として扱わないでください。設定では`ConfigDocument.unsupported_lines`も確認できます。`None`や空リストは未記載・未取得の可能性があり、機器で無効になっていることを保証しません。

## CLIとJSON出力

```shell
nwconfig-parser list
nwconfig-parser list --vendor Fortinet
nwconfig-parser info --vendor Yamaha --os RTX --command "show ip route"
nwconfig-parser parse --vendor Yamaha --os RTX --command "show ip route" --input tests/config/yamaha_rtx_show_ip_route.txt --output route.json
nwconfig-parser validate --vendor Cisco --os "IOS XE" --command running-config --input tests/config/cisco_ios_xe_vrf_running_config.cfg
```

`python -m nwconfig_parser`でも実行できます。`--output`を省略するとJSONを標準出力へ出します。`--os-family`は`--os`の別名です。終了コードは`SUCCESS`が0、`PARTIAL_SUCCESS`が1、`FAILED`またはCLIエラーが2です。`validate`は解析の完全性を確認するもので、実機の動作確認ではありません。
