# Skill Magnet MVP

GitHubで管理された複数のAIスキルを「スキルパック」単位で選択し、CodexとClaude Codeへ安全に配布するローカルCLIです。

## MVPの安全条件

- `allowed_github_owners` に列挙した自分のGitHub所有者だけを受け付ける。
- 設定したGitHub URLとローカルcloneの `origin` が一致しなければ拒否する。
- packごとに完全な `expected_commit` SHAを固定し、cloneのHEADが異なれば拒否する。
- 未コミット変更があるsource repositoryからは配布しない。
- skill内のsymlink・junction、秘密鍵・token・secret状fileを拒否する。
- パックに明示した全skillを一つの選択項目として扱う。
- 未管理の同名directoryを上書きしない。
- 配布後に手編集されたdirectoryを上書き・rollbackしない。
- `dry-run` と `status` は読み取り専用。
- 全skill・全targetを一つのtransactionとしてstageし、`sync` 前の内容をsnapshotとして保存する。
- 配置途中やstate保存が失敗した場合は、pack全体と旧stateを復元する。
- crash時はpending transactionを次の `sync` / `rollback` で回復する。

## 現在のパック

`skill-magnet.json` には、検証済みの `codex-pmo-skills` 9件を一つのパックとして登録しています。

## 実行

Python 3.11以降が必要です。依存packageはありません。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json packs
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json dry-run --pack codex-pmo-skills
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json sync --pack codex-pmo-skills
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json status --pack codex-pmo-skills
python -m skill_magnet --config C:\Projects\skill-magnet\skill-magnet.json rollback --pack codex-pmo-skills
```

`--target codex` または `--target claude` を付けると片方だけを対象にできます。省略時は両方です。

既定の配布先は次の通りです。

- Codex: `~/.agents/skills`
- Claude Code: `~/.claude/skills`
- 状態・rollback snapshot: `~/.skill-magnet`

## テスト

テストは一時directory内にGit repositoryと配布先を作るため、実際のユーザースキルを変更しません。

```powershell
$env:PYTHONPATH = "C:\Projects\skill-magnet\src"
python -m unittest discover -s C:\Projects\skill-magnet\tests -v
```

完了条件は、以下のテストがすべて成功することです。

1. 複数skillを含むpackが一項目として選択される。
2. allowlist外ownerとorigin不一致を拒否する。
3. expected commitと異なるHEADを拒否する。
4. dirtyなsource repositoryを拒否する。
5. symlink・junctionとsecret状file/contentを拒否する。
6. dry-runがfilesystemとstateを変更しない。
7. pack全体をCodexとClaudeへ一transactionで配布し、statusがcurrentになる。
8. 未管理の同名directoryを上書きしない。
9. 配布後のlocal driftを上書きしない。
10. pack同期途中の失敗時に全targetを元へ戻す。
11. process interruption後にpending journalから全targetを回復する。
12. syncのstate保存失敗時に全targetと旧stateを戻す。
13. rollback途中の失敗時に全targetと旧stateを戻す。
14. rollbackのstate保存失敗時に全targetと旧stateを戻す。
15. 更新前のmanaged versionへrollbackできる。
16. 初回配布そのものをrollbackできる。
