# Skill Magnet

Skill Magnetは、ユーザー自身のGitHub保管庫でスキルを保存・版管理し、仕事に必要なスキルパックだけを選んでCodexまたはClaude Codeに渡すためのローカルCLIです。

## 現在の状態

MVPは配布方式を再設計中です。旧MVPの `sync` は `~/.agents/skills` と `~/.claude/skills` への常設コピーを前提としており、現在の製品ポリシーに適合しません。実ユーザー環境では `sync` と `rollback` を実行しないでください。これまで本番 `sync` は実施していません。

## 製品ポリシー

唯一の規範的な定義は [`policy/product-policy.json`](policy/product-policy.json) です。以下は、その `principles_ja` を読みやすく表示したものです。文書と定義の不一致は自動テストで拒否します。

<!-- product-policy:begin -->
- GitHubのユーザー所有保管庫を唯一の正本とする。
- Skill Magnetはスキルの目的に沿って、必要なパックだけを明示選択して呼び出す。
- Codex/Claudeへの全件・常設・暗黙同期を既定にしない。
- 一時ローカル展開が技術的に必要な場合も、対象・理由・期限・cleanupを明示し、検証後に片付ける。
- 保管庫の版・来歴・承認を保持する。
<!-- product-policy:end -->

このポリシーから、次の既定動作が決まります。

- 起動時に有効なパックはゼロです。
- ユーザーは毎回パックと対象ランタイムを明示します。
- GitHub URL、完全なcommit SHA、承認記録を検証してから有効化します。
- 一時展開物には対象、理由、期限、cleanup方法を記録し、検証後または期限到来時に削除します。
- 全パックの一括配布、ユーザー領域への常設配置、バックグラウンドでの暗黙同期は既定機能にしません。

## 想定する利用手順

再設計後のMVPは、次の流れを満たすものとして実装します。コマンド名と引数はまだ確定していません。

1. ユーザー所有GitHub保管庫から利用可能なパックと、その目的・版・承認状態を一覧する。
2. 目的に合うパック一つと、CodexまたはClaude Codeを明示選択する。
3. `dry-run` で取得元commit、対象、展開場所、期限、cleanup予定、競合を確認する。
4. 選択したパックだけをセッションに有効化する。
5. ランタイム上でスキルが認識・呼び出し可能であることを検証する。
6. セッション終了後に一時展開物をcleanupし、残留ゼロを確認する。

`dry-run` を通していない有効化は拒否する設計です。

## ランタイム方式の現状

- Claude Code: 一時プラグインを作成し、`claude --plugin-dir <temporary-plugin>` でそのセッションだけ読み込む案を第一候補とします。スキルは `/pack-name:skill-name` で明示呼び出しできます。
- Codex: ローカルスキルの公式探索先は作業repository配下の `.agents/skills`、ユーザー領域の `$HOME/.agents/skills` などです。任意の一時directoryをセッション限定探索先にする公式CLIオプションは確認できていません。

Codexについては、プロジェクトを変更しない初期プロンプト方式と、一時的に `.agents/skills` を配置してネイティブ認識させる方式のどちらを採るか、ユーザー判断が必要です。比較は [`docs/mvp-redesign.md`](docs/mvp-redesign.md) にあります。

## テスト

現段階のテストは二種類あります。

- 製品ポリシーテスト: 規範的定義が必須制約を保持し、READMEと設計文書の表示が一致することを検証します。
- 旧MVPテスト: 旧常設syncエンジンの安全性を回帰確認します。成功しても、再設計後MVPの完成を意味しません。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
python -m unittest discover -s C:\Projects\skill-magnet\tests -v
```

再設計後MVPの完了条件は、選択パックだけの取得、commit/来歴/承認検証、ランタイム認識、明示呼び出し、期限とcleanup、失敗・中断後の残留ゼロを含む新しいテストがすべて成功することです。現時点では未完成です。
