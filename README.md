# NWConfig Parser

ネットワーク機器から取得した設定ファイルおよびコマンド出力を読み取るための、拡張可能なPythonフレームワークです。共通基盤は実装済みで、現在はCisco IOS/IOS XEの4種類の運用コマンドに対応しています。

## 動作要件とインストール

- Python 3.12以上
- 実行時のサードパーティ依存ライブラリなし

```powershell
python -m pip install -e ".[dev]"
```

`dev`追加オプションではpytest、Ruff、mypyがインストールされます。パッケージは`src/`レイアウトを使用し、`nwconfig-parser`コマンドを提供します。

## アーキテクチャ

- `src/nwconfig_parser/models.py`：共通の型付きデータモデル、解析結果、参照元情報、トポロジーの基本要素、検証ステータス。
- `src/nwconfig_parser/parsers/base.py`：パーサのインターフェースと選択時の例外。
- `src/nwconfig_parser/parsers/registry.py`：明示的な登録、競合検出、照合、パーサの説明情報。
- `src/nwconfig_parser/core/engine.py`：レジストリを利用する解析実行インターフェース。
- `src/nwconfig_parser/input.py`：デコードした原文を保持しながらファイルをデコードし、改行を正規化。
- `src/nwconfig_parser/cli.py`：CLIとJSONシリアライズ。
- `src/nwconfig_parser/parsers/example.py`：Phase 1の一連の処理を検証する合成データ用行パーサ。
- `src/nwconfig_parser/parsers/cisco_operational.py`：IOS/IOS XEの4種類の運用コマンドに固有の解析処理。
- `src/nwconfig_parser/parsers/operational_base.py`、`zebra_routes.py`、`yamaha_operational.py`、`fortinet_operational.py`、`a10_operational.py`、`hpe_comware_operational.py`：Phase 4のYamaha RTX / Fortinet / A10 / HPE Comware向けInterface・Routing解析（すべて合成データのみで検証、実機未検証。詳細は `docs/support-status.md`）。Arubaは未実装。
- `src/nwconfig_parser/parsers/vendor_config_base.py`、`yamaha_rtx_config.py`、`fortinet_fortios_config.py`、`a10_acos_config.py`、`hpe_comware_config.py`：Yamaha RTX / FortiOS / A10 ACOS / HPE Comwareの設定パーサ（hostname・interface IPv4・description・admin state・static route。合成データのみで検証、実機未検証。NX-OS/Arubaは対象外。詳細は [docs/vendor-config-support.md](docs/vendor-config-support.md)）。
- `src/nwconfig_parser/parsers/cisco_config.py`：Cisco IOS/IOS XEのrunning-configの階層構造を構築。`cisco_config_semantics.py`が認識した設定文を共通モデルへ変換し、各ノードを`PARSED`、`UNSUPPORTED`、`INVALID`に分類。
- `src/nwconfig_parser/analysis/prefix_list.py`：Prefix Listを評価し、不完全なリストには`INDETERMINATE`を返す。

データの流れは、InputDocument（原文と正規化済みテキスト）→ ParserEngine → ParserRegistryによる選択 → 選択されたパーサ → ParseResult（解析データ、ステータス、問題点、参照元情報）です。

Registryはパーサの選択と説明を担当し、Engineはパーサの呼び出しとメタデータの補完を担当します。パーサは部分成功や失敗を明示的に報告しなければなりません。パーサ選択時に推測した実装へフォールバックすることはありません。

## CLI

登録済みパーサを一覧表示します。

```powershell
nwconfig-parser list
nwconfig-parser list --vendor Example
```

パーサのメタデータを確認します。

```powershell
nwconfig-parser info --vendor Example --os Synthetic --command "parse text"
```

取得したテキストを解析し、JSONとして出力します。

```powershell
nwconfig-parser parse --vendor Example --os Synthetic --command "parse text" --input .\capture.txt --output .\result.json
nwconfig-parser parse --vendor Cisco --os IOS --command "show ip route" --input .\route.txt --output .\route.json
```

インストール後は`python -m nwconfig_parser parse ...`でも同等の操作ができます。

解析の完全性を検証します。

```powershell
nwconfig-parser validate --vendor Example --os Synthetic --command "parse text" --input .\capture.txt
```

`--os-family`は`--os`の別名、`--version`は`--os-version`の別名です。`--output`を指定しない場合、parseは標準出力にJSONのみを書き込みます。運用上のエラーは標準エラー出力に書き込まれます。終了コードはSUCCESSが`0`、PARTIAL_SUCCESSが`1`、FAILEDまたはCLI・入力・選択エラーが`2`です。

## Python API

```python
from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.parsers.base import ParseContext
from nwconfig_parser.parsers.example import ExampleTextParser
from nwconfig_parser.parsers.registry import ParserRegistry

registry = ParserRegistry()
registry.register(ExampleTextParser())
engine = ParserEngine(registry)
result = engine.parse(
    "sample output\n",
    ParseContext(vendor="Example", os_family="Synthetic", command="parse text"),
)
```

`Parser.parse(text, context)`は`ParseResult`を返します。`SourceReference`は解析データや問題点を元ファイル、コマンド、行情報に関連付けます。モデルはdataclassesを使用し、必要に応じてベンダー固有の拡張情報を属性に保持します。

## Registryとパーサの追加

各パーサは`vendor`、`os_family`、`supported_versions`、`command`、`parser_version`を宣言します。`supported_versions`が空なら任意のバージョンを受け付けますが、同じベンダー・OS・コマンドに登録された別のパーサと競合します。登録競合時は`DuplicateParserError`が発生します。選択候補がない場合や曖昧な場合は`ParserSelectionError`のサブクラスが明示的に発生します。

パーサを追加する手順：

1. 対象ベンダー・OSのモジュールで`Parser`インターフェースを実装する。
2. 機器ファミリー固有の構文と意味解釈をそのパーサ内に保持する。
3. 正確なステータス、問題点、参照元情報を持つ`ParseResult`を返す。
4. アプリケーションのレジストリへ登録する。
5. 実機出力による検証済みとする前に、合成フィクスチャとpytestテストを追加する。

## データモデル

Configパーサの構造解析と意味解釈の配置は[docs/config-parser-architecture.md](docs/config-parser-architecture.md)を参照してください。

共通dataclassesには、Device、Interface、VLAN、Route/RoutingTable、VRF、OSPFProcess/OSPFNeighbor、BGPPeer、ACL、NATRule、VPN、PrefixList/PrefixListEntry、RouteMap/RouteMapEntry、ParseResult/ParseIssue/SourceReference、TopologyNode/TopologyLink/LogicalDomain、VerificationResultがあります。IPv4フィールドには`ipaddress`型を使用します。

`TopologyLink`は接続先が不明な状態と確信度を扱えます。確定済みリンクには両端の機器が必要です。検証ステータスは`PASS`、`FAIL`、`UNKNOWN`、`ERROR`を区別します。

## 実装状況とサポート

実装状況は`IMPLEMENTED`、`TESTED_WITH_SYNTHETIC_DATA`、`VERIFIED_WITH_DEVICE_OUTPUT`、`NOT_IMPLEMENTED`で表します。合成データによるテストは、実機出力による検証の証拠にはなりません。最新の対応表は[docs/support-status.md](docs/support-status.md)を参照してください。

機密情報を除去した実機コマンド出力はまだ提供されていないため、実機出力による検証済みとされたものはありません。ExampleおよびCiscoのパーサは合成データによるテスト済みのみです。コマンド別の対応表と認識できる出力形式も[docs/support-status.md](docs/support-status.md)に記載されています。

## テストと品質

```powershell
pytest
ruff check .
mypy
```

合成テスト入力は`tests/fixtures/synthetic/`にあります。アドレス、ホスト名、認証情報、その他の機密情報を無害化せずに実機の取得データを追加しないでください。

## VRF対応

Cisco IOS/IOS XEを基準実装として、VRF定義・インターフェースVRF割当・VRF別static route/OSPF/BGP、`show ip route vrf`、VRF別経路検索・差分に対応しています。FortiOS（VRF IDのみ）とComware（`vpn-instance`、運用出力のみ）は部分対応で、その他は未実装です。すべて合成データによる検証のみです。詳細は[docs/vrf-support.md](docs/vrf-support.md)を参照してください。

## 対応範囲と制限事項

IOSおよびIOS XEのレジストリキーで実装済みの運用コマンドは`show ip route`、`show interfaces status`、`show ip interface brief`、`show vlan brief`です。未対応の行形式は黙って破棄せず報告します。受け付ける形式と制限事項は[docs/support-status.md](docs/support-status.md)を参照してください。

OSPF/BGPの運用コマンド用パーサ、Cisco設定のより広範な意味解析、他ベンダー対応、デフォルト値の推定、自然言語による確認、意味的な差分比較、トポロジーの自動検出は未実装です。IPv6とLLDPは明示的に対象外です。
