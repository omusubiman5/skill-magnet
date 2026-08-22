# GitHub中心MVP再設計

## 規範

製品ポリシーの唯一の規範的な定義は [`../policy/product-policy.json`](../policy/product-policy.json) です。

<!-- product-policy:begin -->
- GitHubのユーザー所有保管庫を唯一の正本とする。
- Skill Magnetはスキルの目的に沿って、必要なパックだけを明示選択して呼び出す。
- Codex/Claudeへの全件・常設・暗黙同期を既定にしない。
- 一時ローカル展開が技術的に必要な場合も、対象・理由・期限・cleanupを明示し、検証後に片付ける。
- 保管庫の版・来歴・承認を保持する。
<!-- product-policy:end -->

## 最小アーキテクチャ

1. Registry: ユーザー所有GitHub保管庫、パックID、目的、完全なcommit SHA、承認者と承認日時を管理する。
2. Resolver: 明示選択された一パックだけを、固定commitから読み取り専用で検証する。owner、origin、dirty tree、symlink/junction、secretも拒否する。
3. Activation plan: ランタイム、対象project、理由、期限、一時展開先、呼び出し名、cleanup手順を含むdry-run結果を発行する。
4. Runtime adapter: CodexとClaude Codeの違いを吸収し、選択パックを一セッションだけ有効化する。
5. Session journal: source commit、承認、展開物、process、期限、検証結果を記録する。
6. Cleanup/recovery: 正常終了、失敗、中断、期限到来のすべてで一時展開物を回収し、残留ゼロを検証する。

GitHubのclone/cacheは正本ではありません。commitで再生成できる検証済みの一時物として扱います。

## ランタイム制約

### Claude Code

公式CLIの `--plugin-dir` は、インストールせず指定pluginをそのセッションだけ読み込みます。pluginの `skills/<name>/SKILL.md` は名前空間付き `/plugin-name:skill-name` として明示呼び出しできます。このため、選択パックを期限付き一時pluginへ変換し、Claude Code process終了後にcleanupする方式が最小です。

### Codex

Codexはrepository階層の `.agents/skills`、ユーザー領域の `$HOME/.agents/skills`、admin/system位置からローカルスキルを探索します。現行CLIの `--add-dir` は追加の書込可能workspaceであり、スキル探索先を追加するフラグではありません。Claude Codeの `--plugin-dir` と同等のセッション限定ローカルスキル指定は、公式文書とインストール済みCLI helpで確認できませんでした。

## 候補比較

| 方式 | project変更 | ネイティブ認識 | cleanup | 評価 |
| --- | --- | --- | --- | --- |
| ユーザー領域へ常設配置 | なし | 強い | 手動・残留しやすい | 製品ポリシーに反するため既定案から除外 |
| projectの `.agents/skills` へ一時配置 | 一時的にあり | 強い。`$skill` と自動選択が可能 | journalと中断復旧が必須 | Codexで確実性を優先する候補 |
| 絶対path付き初期プロンプトで `SKILL.md` を読ませる | なし | ネイティブselectorには出ない | temp cacheだけ削除 | project無変更を優先する候補 |
| 一時workspaceをCWDにする | 対象projectは無変更 | 一時workspaceのskillは認識 | 容易 | 実作業projectが主workspaceでなくなり権限・文脈が複雑なため不採用 |

## 推奨する確定部分

- GitHub保管庫を唯一の正本とし、完全なcommit SHAと承認を必須にする。
- 既定で何も有効化せず、毎回一パックと一ランタイムを選択する。
- Claude Codeは期限付き一時pluginと `--plugin-dir` を使う。
- activation前にdry-run tokenを発行し、同一commit・同一対象でのみ実行を許可する。
- process終了後cleanupし、次回起動時にも期限切れ・中断journalを回収する。
- 常設installはMVPに含めない。

## 実装前に必要なユーザー判断

Codexで次のどちらを優先するかを決める必要があります。

1. project無変更: 初期プロンプトで選択スキルの絶対pathとcommitを明示し、Codexに読込確認を返させる。ネイティブな `$skill` selectorや自動選択は保証しない。
2. ネイティブ認識: 対象projectの `.agents/skills` に選択パックだけを一時配置し、journal、期限、明示cleanup、中断時の次回回収で残留を防ぐ。projectを一時変更する。

この判断が済むまで、runtime activation、sync、cleanupの実装は変更しません。

## 確認した公式資料

- [OpenAI: Build skills](https://learn.chatgpt.com/docs/build-skills) — Codexの明示・暗黙呼び出し、progressive disclosure、repository/user/admin/systemの探索位置。
- [Anthropic: Create plugins](https://code.claude.com/docs/en/plugins) — `--plugin-dir`、plugin名前空間、`/plugin-name:skill-name`、複数pluginのセッション読込。
- [Anthropic: Plugins reference](https://code.claude.com/docs/en/plugins-reference) — `--plugin-dir` と `--plugin-url` がセッション期間だけ有効であること、install済みpluginとの違い。

加えて、2026-08-22時点のインストール済み `codex --help` と `codex plugin --help` を読み取り確認しました。`--cd`、`--add-dir`、初期prompt、marketplaceからのplugin追加はありますが、Claude Codeの `--plugin-dir` に相当する任意ローカルskill/pluginのセッション限定指定は表示されません。

## 再設計後の最小テスト

- 規範的policyとREADME・設計文書の表示が一致する。
- 既定のactive packがゼロで、packとruntimeの明示選択なしでは拒否する。
- ユーザー所有GitHub、完全commit SHA、来歴、承認不足を拒否する。
- 選択外packを取得・展開・認識させない。
- dry-runが対象、理由、期限、展開先、cleanup、呼び出し名を示し、何も変更しない。
- Claude Codeのsession-only pluginを検証し、明示呼び出しできる。
- 選択したCodex方式でskill読込を検証する。
- 正常終了、途中失敗、強制終了、期限切れ後に一時物とjournalの残留がない。
- ユーザー領域への常設配置と暗黙syncが既定経路から到達不能である。
