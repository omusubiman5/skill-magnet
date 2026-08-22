# Skill Magnet MVP

Skill Magnetは、GitHubで管理している複数のAIスキルを「スキルパック」として選び、CodexとClaude Codeへ安全に配布するためのローカルCLIです。

たとえば9個のスキルを一つずつコピーする代わりに、`codex-pmo-skills` というパックを一項目として選択し、内容を事前確認してからCodexとClaude Codeへまとめて配置できます。

## 何ができるか

基本操作は次の5つです。

| コマンド | 用途 | ファイル変更 |
| --- | --- | --- |
| `packs` | 選択できるスキルパックを一覧表示する | しない |
| `dry-run` | 配布した場合の変更内容を事前確認する | しない |
| `sync` | パックをCodex／Claude Codeへ配布する | する |
| `status` | 配布済み内容が最新か、手編集されていないか確認する | しない |
| `rollback` | 直前の配布transactionを元に戻す | する |

## 始める前の前提

- Windows PowerShellでの実行例です。
- Python 3.12以降とGitが必要です。Windows junctionを確実に検出するため、Python 3.11以前はサポートしません。
- Pythonの追加packageは必要ありません。
- 配布元のGitHub repositoryは、あらかじめローカルへcloneしておきます。
- 使用できるのは `skill-magnet.json` の `allowed_github_owners` に登録した自分のGitHub repositoryだけです。
- 配布元repositoryのworking treeがcleanで、HEADが設定中の `expected_commit` と一致している必要があります。

現在の設定では、次のパックを利用できます。

- パック名: `codex-pmo-skills`
- スキル数: 9
- 配布元: `C:\Projects\codex-pmo-skills-public`
- 固定commit: `ad89222e65a570cbe498eb60550b035ebe90bf61`

## 最初の準備

PowerShellを開き、Skill MagnetのPython moduleを読み込めるようにします。この設定はPowerShellを開き直すと消えるため、新しいPowerShellではもう一度実行してください。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
```

以降の例では、設定ファイルを明示するために毎回次のオプションを使用します。

```text
--config C:\Projects\skill-magnet\skill-magnet.json
```

## 安全な基本手順

必ず `packs` → `dry-run` → `sync` → `status` の順で進めてください。特に、実ユーザー環境へ配布する前の `dry-run` は必須です。

### 1. パックを確認する: `packs`

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json packs
```

出力例:

```json
{
  "packs": [
    {
      "id": "codex-pmo-skills",
      "repo_url": "https://github.com/omusubiman5/codex-pmo-skills.git",
      "skills": 9
    }
  ]
}
```

後続コマンドの `--pack` には、ここで表示された `id` を指定します。

### 2. 変更内容を必ず事前確認する: `dry-run`

CodexとClaude Codeの両方を確認する場合:

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json dry-run --pack codex-pmo-skills
```

`dry-run` は配布元を検証し、各スキルについて予定される処理をJSONで表示します。配布先やstateは変更しません。

`items` の `action` を確認してください。

| action | 意味 | `sync`してよいか |
| --- | --- | --- |
| `create` | 配布先に存在しないため新規作成する | 内容と配布先を確認後に可 |
| `update` | Skill Magnet管理下の旧版を更新する | 可 |
| `restore` | 管理対象が消えているため復元する | 意図した削除でないか確認 |
| `unchanged` | すでに同じ内容がある | 変更なし |
| `conflict` | 同名の未管理directory等がある | 不可。先に競合を解決する |
| `drift` | 配布後の内容がローカルで変更されている | 不可。変更を保全して原因を確認する |

初回の実パックdry-runでは、Codex 9件とClaude 9件の計18件が `create` と表示される想定です。必ず各 `destination` が意図した場所か確認してください。

### 3. 配布する: `sync`

`dry-run` の内容に問題がないことを確認してから、同じパック・同じtarget指定で実行します。

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json sync --pack codex-pmo-skills
```

省略時はCodexとClaude Codeの両方が対象です。既定の配布先は次の通りです。

- Codex: `~/.agents/skills`
- Claude Code: `~/.claude/skills`
- 配布状態とrollback snapshot: `~/.skill-magnet`

パック内の全スキルと全targetは一つのtransactionとして処理されます。途中で一件でも失敗した場合、一部だけ配布された状態を残さず、配置先とstateを実行前へ戻します。

### 4. 配布結果を確認する: `status`

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json status --pack codex-pmo-skills
```

すべての `action` が `unchanged` なら、配布元と配布先の内容は一致しています。

`update` があれば固定commit側の内容が配布済み内容と異なります。`restore` は配布先の欠落、`drift` は配布先の手編集、`conflict` は未管理の同名directoryを示します。原因を確認するまで再度 `sync` しないでください。

## CodexまたはClaude Codeだけを対象にする

`dry-run`、`sync`、`status` は `--target` で対象を限定できます。

Codexだけをdry-runして配布する例:

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json dry-run --pack codex-pmo-skills --target codex
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json sync --pack codex-pmo-skills --target codex
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json status --pack codex-pmo-skills --target codex
```

Claude Codeだけを対象にする例:

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json dry-run --pack codex-pmo-skills --target claude
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json sync --pack codex-pmo-skills --target claude
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json status --pack codex-pmo-skills --target claude
```

`--target` を省略すると両方が対象になります。dry-runとsyncで異なるtargetを指定しないよう注意してください。

## 配布を元に戻す: `rollback`

直前に成功した未rollbackのtransactionを元に戻すには、次を実行します。

```powershell
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json rollback --pack codex-pmo-skills
```

rollbackの動作:

- 初回配布をrollbackすると、そのtransactionで新規作成したスキルdirectoryを削除します。
- 更新をrollbackすると、保存していた更新前snapshotへ戻します。
- rollbackはパックの直前transaction単位です。`rollback` コマンドに `--target` はありません。
- 配布後に対象directoryが手編集されている場合は、手編集を消さないようrollbackを拒否します。
- rollback途中やstate保存で失敗した場合も、rollback開始前の配置とstateへ戻します。

rollback後は、もう一度 `status` を実行して状態を確認してください。配布元が新しいままなら、以前のsnapshotへ戻した項目は `update` と表示されることがあります。これは「現在の固定commitを再配布すれば更新される」という意味です。

## 失敗したときの挙動

Skill Magnetは、次のような場合に処理を拒否します。

- GitHub ownerがallowlistにない。
- 設定したrepository URLとcloneの `origin` が一致しない。
- HEADが `expected_commit` と一致しない。
- 配布元に未コミット変更がある。
- スキル内にsymlink、junction、秘密鍵、token、secret状ファイル／内容がある。
- 配布先に同名の未管理directoryがある。
- Skill Magnetで配布した内容がローカル編集されている。

`sync` は全内容を先にstageしてから置換します。配置途中またはstate保存で失敗した場合は、パック全体を実行前へ戻します。process interruptionで通常の復旧処理を実行できなかった場合はpending transaction journalが残り、次回の `sync` または `rollback` で回復します。

エラーが出たときに、対象directoryを手動で削除して先へ進めないでください。まずエラーメッセージ、`status`、Gitの状態を確認し、必要なファイルを退避してから原因を解消します。

## 配布元commitを更新する場合

Skill Magnetは意図しない最新版を自動配布しません。配布元repositoryを更新した場合は、新しいcommitをレビューしたうえで `skill-magnet.json` の `expected_commit` を完全な40文字SHAへ更新します。

その後、必ず次の順で確認します。

1. 配布元repositoryがcleanであることを確認する。
2. `dry-run` で `update` 内容を確認する。
3. `sync` を実行する。
4. `status` がすべて `unchanged` になることを確認する。

## 現在の制約

- MVPはローカルCLIです。GUIはありません。
- GitHubからのclone、pull、認証、release取得は自動化していません。
- パック登録と `expected_commit` の更新は `skill-magnet.json` を手動編集します。
- 現在登録済みなのは `codex-pmo-skills` 一パックです。
- rollbackできるのは、保存済みsnapshotがある未rollbackのtransactionです。任意の過去versionを選ぶ機能はありません。
- secret検査は高確度のファイル名・token形式・秘密鍵形式を拒否しますが、人による内容レビューの代わりにはなりません。
- 自動テストは一時directory内の疑似Codex／Claude配布先で実施済みです。
- **実ユーザーの `~/.agents/skills` と `~/.claude/skills` に対する本番 `sync` は、まだ実施していません。** 実施前に必ず実環境の `dry-run` 結果をレビューしてください。

## テストと完了条件

テストは一時directory内にGit repository、Codex／Claude配布先、state領域を作ります。実際のユーザースキルは変更しません。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
python -m unittest discover -s C:\Projects\skill-magnet\tests -v
```

MVPの完了条件は、次を含む全自動テストが成功することです。

1. 複数スキルを含むパックを一項目として選択できる。
2. allowlist外owner、origin不一致、expected commit不一致、dirty sourceを拒否する。
3. symlink／junctionとsecret状ファイル／内容を拒否する。
4. dry-runが配布先とstateを変更しない。
5. パック全体をCodexとClaudeへ一transactionで同期できる。
6. 未管理の同名directoryと配布後のlocal driftを上書きしない。
7. sync途中、process interruption、sync state保存失敗から全体を復旧する。
8. rollback途中、rollback state保存失敗から全体を復旧する。
9. 更新前versionと初回配布前の状態へrollbackできる。

現在のMVPでは24テストすべてが成功し、skipはありません。

```text
Ran 24 tests
OK
```

実パックの読み取り専用dry-runも成功し、Codex 9件＋Claude 9件の計18件が `create` と判定されています。本番syncは行っていません。
