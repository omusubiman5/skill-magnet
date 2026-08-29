# Codex対象誤りの原因調査 — 2026-08-29

## 結論

製品が「Codex」を選択した後の実行先をCodex Desktop appではなく、非対話のCodex CLI verification processとして実装していた。利用者の目的はDesktop appの新規taskで依頼を継続することだったが、実装はstructured JSONを生成・検証してSkill Magnet独自の結果windowを出す別製品フローになっていた。

## 最初の不一致

- `src/skill_magnet/cli.py` の旧context分岐は、確認済みcontractをruntimeに関係なく `ActivationEngine.execute(..., interactive_handoff=True)` へ渡していた。
- `src/skill_magnet/activation.py` の `execute` はCodex contractに対し `codex exec --json --output-schema ...` をsubprocess起動していた。
- `src/skill_magnet/ui.py` の旧Codex web分岐はDesktop appへ渡さず、Codexの対話surfaceがないとして拒否していた。

一方、成功比較 `C:\Projects\news-obsidian-pipeline\src\lib\skill-workflow.mjs:53` と `public\task-board.js:34` は `codex://threads/new?path=...&prompt=...` を使ってCodex Desktop appの新規taskを作る。このtarget差が根本原因である。

## なぜ誤った状態で進んだか

1. 「skill適用を機械検証する」という内部品質目標を、利用者が操作するCodex targetの選択より優先した。
2. fake runtimeと実Codex CLIのtestを完成gateにし、Desktop app taskのprompt/回答をgateに含めなかった。
3. `verified_completed`という状態名がCLI verificationの成功を製品完了に見せ、Desktop handoff未実装を隠した。
4. terminal screenshotやSkill Magnet結果windowを証拠に使い、Codex Desktop app chrome、task prompt、自然文回答を要求しなかった。
5. 成功比較repoのdeep-link実装を設計着手時に正本比較しなかった。

## 影響範囲

- ExplorerからCodexを選んでもDesktop appの新規taskにならなかった。
- CLI/TUI、MCP警告、structured JSON、console抑止など、本来のDesktop UXに不要な問題を製品経路へ持ち込んだ。
- 過去のCodex CLI `verified_applied` / `verified_completed`、terminal画像、test summary画像はDesktop版の完成証拠として無効である。
- Claude adapterは別分岐であり、このtarget修正の対象外である。

## 完了判定の制約

OSがdeep linkを受理したことはDesktop taskの回答完了を証明しない。Desktop task resultを製品が正式に機械取得できない現状では、Skill Magnetは `desktop_handoff_ready` までを記録し、`verified_completed`を生成してはならない。実回答の確認はDesktop app task自体の実機証拠として分離する。

実機のinstalled Codex app `26.820.10647.0` の`app.asar`もread-only確認した。new-thread deep-link parserが受け付ける値は`prompt`、`browserUrl`、`mode`、`originUrl`、`path`、`projectId`で、送信を自動実行するparameterはない。したがって`codex://threads/new`は新規task composerのprefillであり、利用者の送信操作前にはtask IDが作られない。GUI自動操作を使わない条件では、送信ボタンの手動1回がDesktop実行の明示確認gateになる。
