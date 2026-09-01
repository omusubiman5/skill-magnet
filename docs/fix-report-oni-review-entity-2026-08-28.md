# `（鬼レビュー対応）&#x20;` 表示 対応報告

> 2026-09-01追記: この対応の表示専用正規化だけではhandoff本文のliteral entityを除去できなかった。依頼確定境界まで拡張した現行対応は[`fix-report-actual-request-u0020-2026-09-01.md`](fix-report-actual-request-u0020-2026-09-01.md)を参照すること。

日付: 2026-08-28  
対象: `C:\Projects\skill-magnet`  
状態: partial（実装・自動test完了、実画面受入未完了）

## 変更

### 表示専用の限定正規化

`src/skill_magnet/core.py` に `normalize_display_text` を追加した。HTML entity全般をdecodeせず、numeric U+0020だけを半角空白へ変換する。

- `&#x20;` / `&#X20;` → 半角空白
- `&#32;` → 半角空白
- `&lt;保持&gt;` → `&lt;保持&gt;` のまま

`Pack.skill_display_name` と `Pack.skill_purpose` の戻り値へ適用し、Windows modern/classic menu manifestと確認UIの表示名・用途へ同じ境界を使うようにした。raw configは変更しない。

`src/skill_magnet/ui.py` の `context_ui_text` ではformat後の表示文字列に同じ正規化を適用した。actual requestを含む確認dialogではliteral `&#x20;` を見せず、contract作成へ渡す `purpose` は元の文字列のまま保持する。

### 回帰test

`tests/test_activation.py` に次を追加した。

1. confirmation表示で `（鬼レビュー対応）&#x20;` が `（鬼レビュー対応） ` となり、`&lt;保持&gt;` はdecodeされない。
2. entityを含むactual requestがlaunch contractで1文字も変更されない。
3. skill表示名・用途はmenu境界でU+0020だけ正規化され、config fileにはraw値が残る。

## 検証結果

Focused test:

```text
python -m unittest \
  tests.test_activation.ActivationEndToEndTest.test_context_display_normalizes_only_u0020_numeric_references \
  tests.test_activation.ActivationEndToEndTest.test_context_contract_keeps_entity_bearing_actual_request_exact \
  tests.test_activation.ActivationEndToEndTest.test_menu_display_normalizes_space_reference_without_decoding_markup \
  tests.test_activation.ActivationEndToEndTest.test_context_ui_confirmation_preserves_request_and_internal_values

Ran 4 tests in 2.483s
OK
```

Full suite:

```text
python -m unittest discover -s tests

Ran 103 tests in 123.816s
OK (skipped=1)
```

構文compileとdiff check:

```text
python -m compileall -q src tests
exit 0

git diff --check
exit 0
```

`git diff --check` は既存tracked fileについて「次回Gitが触れるとLFをCRLFへ置換する」warningを表示した。whitespace errorはなく、warningと失敗を区別した。

## 影響範囲

- internal skill ID、pack ID、repository、commit、digest: 変更なし
- actual request、contract、task envelope、SHA-256: 変更なし
- menu/UIの表示名・用途・confirmation: numeric U+0020だけ表示上の空白へ変換
- HTML entity全般: decodeしない
- install/bootstrap: 下記の単一update transactionで反映済み。Python sourceはinstalled menuの固定bootstrapからcurrent repositoryを参照する。

## Installed update transaction

事前readback:

- install root: `C:\Users\HOMEA\AppData\Local\SkillMagnet\ContextMenu`
- original rollback: 3 files、7,970 bytes、tree SHA-256 `fad5c98933a12396d0f0e78fe14b43457e4aceceea59be53bd86aecf4edc7e30`
- active `ContextMenu.rollback.update`: なし
- modern: `usable_installed_state=true`
- classic/legacy registry root 4件: すべてなし

実行は次の1回だけで、exit 0だった。

```text
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json install-context-menu --platform windows --confirm
```

transaction出力はmodern `installed=true`、`usable_installed_state=true`、classic `installed=false`、`fallback_while_modern_unavailable=false` を返した。

事後readback:

| artifact / state | current / expected | installed / actual | 判定 |
|---|---|---|---|
| `SkillMagnetCommand.dll` SHA-256 | `249924aa6cfe7680b60d8b74f9dd6864026d51677355f8b41be0f14176f87e41` | `249924aa6cfe7680b60d8b74f9dd6864026d51677355f8b41be0f14176f87e41` | match |
| `SkillMagnetLauncher.exe` SHA-256 | `d6f780dcaacbb478c1d94a82587470247a4bc69b9e81ac72c66890266f7ffe26` | `d6f780dcaacbb478c1d94a82587470247a4bc69b9e81ac72c66890266f7ffe26` | match |
| `AppxManifest.xml` SHA-256 | `4db2d9134b7aa26c20d3ba631cc6bb917eee0d101ad0b2fdb8b917687e035c3a` | `4db2d9134b7aa26c20d3ba631cc6bb917eee0d101ad0b2fdb8b917687e035c3a` | match |
| installed `SkillMagnetMenu.tsv` SHA-256 | transactionでcurrent configから生成 | `c5b53ea632844e8d713feace9d1d308a56e496f73cea2bda695c7fdbfa4b6a38` | recorded |
| menu contract | v3 / 9 leaves | v3 / 9 leaves | match |
| bootstrap source reference | `C:\Projects\skill-magnet\src` × 9 | 9件 | match |
| config reference | `C:\Projects\skill-magnet\skill-magnet.json` × 9 | 9件 | match |
| literal `&#x20;` in installed menu | 0 | 0 | match |
| package usable state | true | true | match |
| classic/legacy root | 4件とも不存在 | 4件とも不存在 | match |
| active update | 不存在 | 不存在 | match |
| original rollback tree SHA-256 | `fad5c98933a12396d0f0e78fe14b43457e4aceceea59be53bd86aecf4edc7e30` | 同値、3 files / 7,970 bytes | unchanged |

current Python source SHA-256:

- `src/skill_magnet/core.py`: `d2e2d65ea988a5b8f61558c4b65e970ef9965e7f75c0b7d97b05e9952c94dd47`
- `src/skill_magnet/ui.py`: `19f08d1e87916471ded97a889b7244a55943de22cd2c54a2c5470ed28ada8f38`
- `src/skill_magnet/platforms.py`: `fc9d2aac225d6043f22447763ed5ffe3720a172b6d46c33c82197e302a117425`
- `src/skill_magnet/cli.py`: `6f1ec31aabacd355d84c30f74e8aa02badca0db45d8efed3904c3eb1b07f26bb`
- `skill-magnet.json`: `4eaf92e0c3824b41c1861f32b6926cee309269020ec7f1eef1a73fc5f5b8b37f`

process非生成focused smokeは、U+0020表示正規化、一般entity保持、input原文保持を同一Python process内でassertし、PASSした。smokeコードによるchild process生成は0。install後にSkill Magnetをcommand lineへ持つ残存Python/pythonw/Codex/Claude/cmd/Windows Terminal processは0件だった。

## 未完了

- Explorer/Tk実画面で `&#x20;` が露出しないことの手動受入
- 観測された直接Codex user messageの入力前生成元（clipboard/外部HTML/手入力）は証拠がなく不明

このため製品完成または実機修正完了とは宣言しない。
