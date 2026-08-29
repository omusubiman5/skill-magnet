# Codex Desktop target対応報告 — 2026-08-29

## 変更

- `src/skill_magnet/activation.py:599` に、人が読めるDesktop task promptを追加した。選択skill ID、instruction全文、actual request、非デモ実行、期待成果、contract/attempt、actual-request/instruction/acceptance digestを同じrunへ束縛する。内部JSONを最終回答として要求しない。
- `src/skill_magnet/activation.py:632` にCodex Desktop handoff準備を追加した。固定commitとrepositoryを再検証し、一回限りcontractを消費する。
- `src/skill_magnet/activation.py:663` にhandoff証拠を追加した。状態は `desktop_handoff_ready`、`desktop_result_verification: not_available`、`verified_completed: false` である。
- `src/skill_magnet/ui.py:363` にcanonical deep link builderを追加した。`path`と`prompt`を独立してencodeし、日本語、改行、空白、`&`、`#`を保持する。
- `src/skill_magnet/ui.py:380` はWindows protocol handlerを `os.startfile` で呼び、Codex CLI、cmd、Windows Terminalを起動しない。
- `src/skill_magnet/cli.py:206` と `:242` のcontext Codex分岐をDesktop handoffへ変更した。Claude分岐は既存runtime adapterを維持した。
- READMEと`docs/mvp-redesign.md`をDesktop app target、handoff状態、過去CLI証拠の非採用へ統一した。

## 回帰検証

- focused Desktop handoff / encoding / CLI non-invocation / state tests: 7 PASS。
- `python -m unittest tests.test_activation`: 71 tests、PASS（1 skip）。
- `python -m unittest tests.test_product_policy`: 8 tests、PASS。

- full repository suite: 107 tests PASS、1 skip。
- native C++ contract: `SkillMagnet IExplorerCommand contract PASS`。
- native Python host contract: `SkillMagnet IExplorerCommand contract PASS (Python host)`。
- `git diff --check`: whitespace errorなし（既存のLF→CRLF warningのみ）。

## 実Desktop handoff

- product handoffを2026-08-28T23:40:22Zに1回発行した。
- contract ID: `82e1d2b29e26450cab1c57e7c1ed245a`
- attempt ID: `79870dabc4af45c0b4f5fb202d32904f`
- actual-request SHA-256: `6a7251459069b39f9f6904ec38d1007de917d2c6507838a0c2a2d85ed186c27d`
- prompt SHA-256: `7693bc2e1bc7372d8608149501ade6a29321766388d890476d4cfeab644fa395`
- state: `desktop_handoff_ready`、`verified_completed: false`
- global config SHA-256 before/after: `D66F5F60167F696A03D346270C78DD933C9447C3BDC7DE83B897A805A9FB2551`（不変）
- installed modern packageは`usable_installed_state: true`。installed `SkillMagnetMenu.tsv` のlauncher bootstrapは `C:\Projects\skill-magnet\src` をlive参照しているため、Python-only変更にpackage再登録は不要。通常rootは既存の`Skill Magnet`一つを維持する。

deep link発行後のtask一覧に新規task IDはまだ現れなかった。installed appのparserにauto-submit parameterはなく、composer prefill後の送信は利用者の明示操作が必要である。Computer Useは禁止のため、送信ボタンの手動1回を待つ。task ID、回答、PNG/hashは送信・完了後に追記する。

## 完成証拠から除外するもの

- `docs/test-evidence/real-codex-visible-e2e-20260829/` のCLI/terminal画像。
- 過去のCodex CLI 0.148.0による `verified_applied` / `verified_completed`。
- test harnessが生成したsummary画面。

これらはCLI adapterの回帰資料としてのみ保持し、Codex Desktop版のship証拠には使わない。

## 未完了

- product handoffによる実Codex Desktop新規taskの完了待ち。
- Desktop task ID/title、prompt binding、自然文回答の確認。
- Codex Desktop app window矩形だけのPNG captureと目視確認。
- Explorer入口の最終手動受入。
