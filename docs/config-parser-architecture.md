# Configパーサの責務と配置

設定解析は「入力の階層を作る処理」と「設定の意味を解釈する処理」に分けています。全ベンダーで公開の結果は`ParseResult.data`内の`ConfigDocument`です。

| ベンダー / OS | パーサ・階層解析 | 意味解釈 |
|---|---|---|
| Cisco IOS / IOS XE | `cisco_config.py` | `cisco_config_semantics.py` |
| Yamaha RTX | `yamaha_rtx_config.py` | `yamaha_rtx_config_semantics.py` |
| Yamaha SWX | `yamaha_swx_config.py` | `yamaha_swx_config_semantics.py` |
| Fortinet FortiOS | `fortinet_fortios_config.py` | `fortinet_fortios_config_semantics.py` |
| A10 ACOS | `a10_acos_config.py` | `a10_acos_config_semantics.py` |
| HPE Comware | `hpe_comware_config.py` | `hpe_comware_config_semantics.py` |

ファイルは`src/nwconfig_parser/parsers/`にあります。表は役割の配置を示し、全構文対応を意味しません。対応項目は[ベンダー別対応状況](vendor-config-support.md)と[サポート状況](support-status.md)を参照してください。

## 処理の流れ

1. `ParserEngine`が`ParseContext`をRegistryへ渡し、vendor・OS・commandなどでパーサを選択する。
2. ベンダーのconfigパーサが入力から行番号付きの`ConfigNode`階層を作る。
3. ベンダーのconfig Interpreterが階層を読み、`Interface`、`Route`、`VRF`などの共通モデルへ変換する。
4. パーサが解析件数・issueからステータスを決め、診断と入力元情報を含む`ParseResult`を返す。

FortiOSは`config/edit/next/end`の明示的なブロック、RTXは主に行形式、ACOSとComwareは各OSの区切りとインデントを扱います。Ciscoの構文解釈を他ベンダーへ流用しません。

## 共通処理

`vendor_config_base.py`の`VendorConfigParser`はCisco以外のconfigパーサの基底で、入力正規化・空入力・ステータス判定を担当します。`ConfigInterpreter`はノードの解析状態、issue、未対応行、入力元情報などの記録を担当します。共通のIPv4検証やインデント階層生成もこのモジュールにあります。

Ciscoは既存の独立したパーサとInterpreterを使用しています。共通の結果モデルを使いますが、基底クラスまで統一したわけではありません。FortiOSの字句処理ヘルパーは、その階層解析と意味解釈の両方から使用します。

## 機能を追加するとき

- 新しい設定の意味・値の検証・共通モデルへの格納は、そのベンダーの`*_config_semantics.py`へ追加する。
- 設定ブロックや区切りの読み方は、そのベンダーの`*_config.py`を変更する。
- 診断記録などOSに依存しない処理は`vendor_config_base.py`で扱う。
- 新しいパーサは`cli.py`の`build_registry()`へ登録する。
- 共通モデルは`models.py`で管理し、engineにベンダー固有の構文処理を置かない。

利用側はパーサを通して`ConfigDocument`を取得します。Interpreterを直接呼ぶ必要はありません。未知の設定行を階層へ格納しただけでは解析成功にせず、`UNSUPPORTED`や`INVALID`と診断情報を保持します。
