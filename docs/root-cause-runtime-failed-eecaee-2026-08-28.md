# runtime_failed eecaee root cause

## 判定

contract `eecaee60593642ce889b8a35caf180d2` / attempt `79dac29d52d34ae69457a21308c15954` の具体原因は、認証、MCP、sandbox、network到達性ではない。Skill MagnetがCodex 0.148.0へ渡したstrict JSON Schemaで、`result.properties`に宣言した`changes`と`saved_paths`を`result.required`へ含めていなかった。Codex APIはHTTP 400 / `invalid_json_schema`として拒否し、runtimeはexit 1になった。

## Timeline

| UTC | 事実 |
|---|---|
| 2026-08-28 12:16:57 | 元contractを消費。PID 13488のruntime開始後、terminal status `runtime_failed`。旧negative evidenceにはexit code・stderr hash・failure classがなかった。 |
| 2026-08-28 12:29:57 | 元contractから同じactual requestと旧prompt hash `c0a584...`を再構成し、Codex 0.148.0 + 現行user config + 3 MCP process限定disableで1回だけ再現開始。 |
| 2026-08-28 12:30:03 | local Codex sessionのterminal errorがHTTP 400、`invalid_json_schema`、missing required property `changes`を記録。reproductionはexit 1。 |
| 2026-08-28 12:31:25 | schema修正後、同じcontract/actual request相当を`--ephemeral`で1回smoke。 |
| 2026-08-28 12:31:43 | exit 0、`verified_completed`、runtime PID 4464終了。 |

## 最初の不一致

- 修正前の`src/skill_magnet/activation.py`の`_output_schema`は、`result.properties`へ`task_output`、`saved_paths`、`changes`とskill固有fieldを宣言した一方、`required_result_fields`を`task_output`だけで開始していた。
- Codex 0.148.0のresponse formatは、objectの`required`へ`properties`の全keyを列挙するstrict schemaを要求する。
- 最初の拒否はmodel出力後のlocal parserではなく、model実行前のAPI schema validationで発生した。従って`output_failed`ではなくruntime非0の`runtime_failed`である。
- 現在の修正位置は`src/skill_magnet/activation.py:646-726`、runtime分類と保存は同file `72-117`、`961-966`、`1055`。

## なぜfake testが見逃したか

`tests/test_activation.py:414-432`のfake Codexは`--output-schema`をAPIへ送らず、`--output-last-message`のfileへ任意JSONを直接書く。従ってstrict schemaそのものの妥当性を検査していなかった。argv testもprocess限定MCP overrideとcreation flagを確認するだけで、Codex 0.148.0/APIのschema validationは通していなかった。

## Failure evidenceの欠陥

元negative evidenceは`runtime_failed`だけを保存し、exit code、stderr、stdout eventをcleanupした。このため元attempt単体ではconfig、auth、network、schemaのどこで止まったか確定できなかった。

修正後はruntime非0時に次だけを保存する。

- exit code
- allow-listされたfailure classと固定文summary
- stderr/stdoutのpresenceとSHA-256

raw stderr/stdout、credential、URL、path、flag本文はnegative evidenceへ保存しない。CodexのJSONL stdoutにのみ出る`invalid_json_schema`も分類対象にし、raw eventを残さず具体分類を保持する。

## 影響範囲

修正前のCodex verificationはすべて同じ共通result schemaを使うため、選択skillに関係なくCodex 0.148.0のstrict validationで拒否され得た。成功fake test、UI、native menu登録の成功は、このAPI境界の成功を証明しない。contract/acceptance/actual-request binding自体は原因ではなく、緩和していない。

## Sanitized evidence

- `test-evidence/runtime-failed-eecaee/0bca440be0544c04a712d11ad2a733b8-manifest.json`
- `test-evidence/runtime-failed-eecaee/reproduction-diagnostic.json`
- `test-evidence/runtime-failed-eecaee/d43b7b53b8b14f4c80cf13146180a260-manifest.json`

