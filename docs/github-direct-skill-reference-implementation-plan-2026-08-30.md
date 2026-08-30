# GitHub直接skill参照への切替実装計画

## 目的

Skill Magnetがskill本文、INDEX、acceptance、skill repository snapshotをローカルディスクへ保存・複製・materializeしない構成へ変更する。ユーザー所有GitHub repositoryの完全なcommit SHAを唯一のskill content sourceとし、Codex Desktopにはそのcommitへ固定したGitHub URLとSHA-256だけを渡す。

## 違反していた現行経路

現行実装には次のローカルskill配置経路があった。

1. `.approved-snapshots/` のGit submoduleにskill repositoryをcheckoutする。
2. wheel build時にsnapshotを `skill_magnet/_packs/` へ複製する。
3. install後のsite-packagesからローカルpackを参照する。
4. Desktop handoff時に `~/.skill-magnet/desktop-materializations/<contract_id>/` へINDEXとskill directoryを複製する。
5. promptへ上記ローカル絶対pathを注入する。

これらをすべて廃止する。

## 不変条件

- skill contentをrepository、build、wheel、site-packages、state directory、project directoryへ保存しない。
- GitHubのユーザー所有repositoryと完全な40文字commit SHAを正本とする。
- branch、tag、HEADなど可変参照をskill content URLへ使わない。
- GitHubから取得した検証用bytesはprocess memory内だけで扱い、終了時に破棄する。
- API key、GitHub token、従量課金APIを要求しない。公開GitHub HTTPSだけを使う。
- Codex Desktop promptには、固定commitのINDEX/SKILL.md URLと各file SHA-256を含める。
- GitHub取得失敗、commit不一致、欠落file、symlink、不正path、size超過、digest不一致ではfail closedにする。
- launch contract、event、evidenceにはprovenance、URL、digestだけを保存し、skill本文を保存しない。

## 実装方針

### 1. Repository resolver

- `repo_url` からallowlist済みowner/repositoryを解析する。
- `https://codeload.github.com/<owner>/<repo>/tar.gz/<full-commit>` をHTTPSで取得する。
- response全体を上限付きでメモリへ読み、`tarfile`でメモリ上だけに展開する。
- regular fileだけを許可し、symlink、hardlink、絶対path、`..`、重複pathを拒否する。
- INDEX、各skillの`SKILL.md`と`acceptance.json`をbytes mappingとして保持する。

### 2. 検証モデル

- filesystem `Path`を前提にしたpack検証を、immutableなin-memory `GitHubPackSnapshot`へ置き換える。
- frontmatter、skill name、description、acceptance schema、INDEX関係、SHA-256をmemory bytesから検証する。
- contractにはrepository URL、commit SHA、INDEX digest、skill directory digest、instruction/acceptance digestを保持する。

### 3. Desktop prompt

- ローカルpath一覧を削除する。
- INDEX URLは `https://raw.githubusercontent.com/<owner>/<repo>/<commit>/INDEX.md` とする。
- SKILL URLは `https://raw.githubusercontent.com/<owner>/<repo>/<commit>/<skill>/SKILL.md` とする。
- URLごとに検証済みSHA-256を併記し、Codexへ全fileの取得・全文読了・digest照合を要求する。
- 実際の依頼、trigger/boundary、INDEX relation、最低1skill適用という既存契約は維持する。

### 4. ローカル配置コードの撤去

- `.approved-snapshots` submoduleと`.gitmodules`を削除する。
- configの`source: package://...`を削除する。
- setup/buildのpack copy、snapshot manifest生成、`_packs` package dataを削除する。
- `_materialize_desktop_pack`、materialization生成・期限管理、handoff evidenceの`materialization`を削除する。旧版残留を一方向に削除するmigration purgeだけは残す。
- README、product policy、repository contractをGitHub直接参照へ更新する。

### 5. テスト

- network transportをfixture bytesで置換し、ディスクへskill fileが作られないことを検証する。
- pinned commit以外、redirect先不一致、oversize、archive traversal、symlink、欠落INDEX/SKILL/acceptanceを拒否する。
- promptが固定GitHub URLとdigestを含み、ローカルskill pathを含まないことを検証する。
- wheelに`SKILL.md`、`acceptance.json`、`INDEX.md`、`_packs`が含まれないことを検証する。
- state directoryとproject directoryにskill contentが残らないことを検証する。
- Windows/macOSの既存テストsuiteを実行する。

## 実機受入

1. 修正版をローカルinstallする。
2. Skill MagnetからCodex Desktop新規taskを起動する。
3. task入力欄に固定commitのGitHub INDEX/SKILL URL、digest、actual requestが入っていることを確認する。
4. `~/.skill-magnet`、site-packages、project、wheelにskill本文が存在しないことを再確認する。
5. Codex Desktopのprompt表示画面をスクリーンショットとして `docs/test-evidence/github-direct-skill-reference-20260830/` に保存する。

## 完了条件

- ローカルskill実体が0件である。
- 製品コードにskill repositoryのcheckout、copy、materialization経路がない。
- Codex Desktop promptが固定GitHub URLを参照している。
- 自動テストが通る。
- 実機スクリーンショットと実績報告書が作成されている。
