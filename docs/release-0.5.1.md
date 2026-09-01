# Skill Magnet 0.5.1 ローカルリリース報告

日付: 2026-09-01

## 判定

**GO — ローカル自己署名版0.5.1**

このGOは、repositoryからwheelを取得し、利用者端末でWindows native componentをbuildしてローカル自己署名する既存配布範囲に限定する。Microsoft Store、PyPI一般公開、正式publisher署名、macOS notarized配布は主張しない。

## 修正内容

1. 実依頼に含まれるnumeric U+0020参照を依頼確定境界でcanonicalizeした。
2. launch contract、依頼SHA-256、Codex/Claude promptを同じcanonical依頼へ統一した。
3. pending transaction journalのID、mode、pack、cleanup policy、target、skill、destination、stage、backup、snapshotを検証し、管理対象外pathを削除前に拒否するようにした。
4. 製品化ゲートへversion、配布scope、公開配布非主張、Desktop完了非主張を追加し、ローカル自己署名版のPASSを公開版GOへ読み替えないようにした。
5. Python packageとWindows MSIX identityを0.5.1へ同期した。

## 検証

- `python -m unittest discover -s tests -v`
  - 139 tests PASS
  - 1 skip: 実行環境に`pythonw.exe`がない既知の環境依存テスト
- 改ざんjournalの管理対象外canary削除拒否: PASS
- entity-bearing actual requestのcontract・SHA・Desktop prompt統一: PASS
- Python 0.5.1 / MSIX 0.5.1.0同期: PASS
- wheel build: PASS

## 成果物

- `dist/skill_magnet-0.5.1-py3-none-any.whl`
- wheel SHA-256: `1335abceda94b20955ebf877330695e41dac6a84530c0a0a788b5e5eee0e8fda`
- logical payload SHA-256: `c463dc6bacaa10dd8959e3e5c63119a021073f56f5d6de8d1c0c652e1d20be32`

## 明示的な配布境界

- `CN=Skill Magnet Local`による自己署名はローカル版専用である。
- 公開publisher identityを持つ配布物とは扱わない。
- Codex/Claudeへのhandoff成功を回答完了とは表示しない。
- macOSの実Finder UIまたはnotarizationを、このWindowsローカル版のGO証拠へ転用しない。

この境界により、利用可能なローカル版を未完了扱いのまま放置せず、同時に未実証の公開配布を誤ってGOにしない。
