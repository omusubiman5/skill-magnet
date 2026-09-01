# GitHub直接skill参照への切替 実績報告

## 結果

Skill Magnet 0.5.0を、skill contentの保管先を設定済みユーザー所有GitHub repositoryだけに限定する構成へ変更した。ローカルにはlaunch contract、event、evidenceなどのJSON metadataだけを許可し、INDEX、SKILL.md、acceptance.json、repository snapshot、materializationを保存しない。

## 削除したローカルskill content

- tracked submodule `.approved-snapshots/codex-delivery-assurance-8f12af5`を削除した。
- `.gitmodules`を削除した。
- Git内部の旧submodule cache `.git/modules/.approved-snapshots/codex-delivery-assurance-8f12af5`をRecycle Binへ移動した。
- build/distに残っていた旧bundled packを削除した。
- Python site-packagesの旧0.4.1 packageをuninstallし、skill contentを含まない0.5.0へ置換した。
- `C:\Users\HOMEA\.skill-magnet\desktop-materializations`を削除した。
- project内の過去E2E残留 `.e2e-state/delivery-assurance-desktop-20260830/desktop-materializations`をRecycle Binへ移動した。

Gitで管理されていたsubmodule削除はGitから復元可能であり、Recycle Binへ移した2ディレクトリもBinを空にするまでは回復可能である。ただし、現行実装と現行stateはどちらも参照しない。

## 実装内容

1. `skill-magnet.json`からローカル`source`を削除し、GitHub repository URLと完全な40文字commit SHAだけをpack identityとした。
2. `https://codeload.github.com/<owner>/<repo>/tar.gz/<commit>`から上限付きで取得し、archiveをprocess memory内だけで検証するresolverを実装した。
3. archive redirect、path traversal、symlink/hardlink、重複path、file/archive展開size超過、欠落file、secret-like contentをfail closedにした。
4. Desktop promptのINDEX/SKILL参照を `raw.githubusercontent.com/<owner>/<repo>/<commit>/...` へ変更し、各fileのSHA-256を併記した。
5. wheel build時のsubmodule検証、`_packs` copy、snapshot manifest生成を削除した。
6. Desktop handoff時のmaterialization作成を削除し、旧materializationはpublic entry時に削除するmigration cleanupだけを残した。
7. CLIのpersistent `sync` はoverrideなしで恒久的に拒否するよう変更した。
8. product policy、README、MVP設計、skill repository contractをGitHub-only storageへ更新した。
9. Python packageとMSIX identityを0.5.0へ更新した。

## 自動検証

- `python -m unittest discover -s tests -p "test_*.py"`
  - 138 tests PASS
  - 1件skip（環境依存）
- GitHub archive専用回帰試験
  - 固定commit codeload URL
  - disk書込み0
  - traversal拒否
  - link拒否
  - redirect拒否
- final wheel
  - `C:\Projects\skill-magnet\dist\skill_magnet-0.5.0-py3-none-any.whl`
  - SHA-256: `99addeeafde2933af9feab15848e90d835518fe5eba15ad1d0c714eba460eca4`
  - 23 members
  - `SKILL.md`、`acceptance.json`、`INDEX.md`、`_packs` members: 0
- 0.5.0 wheelをWindowsのPython 3.12へ実install済み。

## 2026-08-31 目的とpromptの整合

- README先頭へ、GitHub skillをLLMに読ませるだけでなく実際の依頼へ適用して完成成果を得ることを製品目的として明記した。
- INDEXを必須から任意へ変更し、存在するpackだけで関係を検証・読了・適用するようにした。
- Codex DesktopとClaudeの製品promptを共通の人間向け実行promptへ揃えた。
- 「読む・要約する・候補を挙げるだけ」を未実行と明記し、選んだskillの手順・判断基準・境界を分析、編集、生成、検証、最終成果へ反映するよう強制した。
- 自然文、JSON、コード、ファイル等の成果形式を一律禁止する文言と、Skill Magnet用JSONを作成させない方法制限を製品promptから削除した。

## 2026-08-31 Windows error 267修正

- 実右クリック経路で `CreateProcessW` が選択projectを `lpCurrentDirectory` にも渡し、Windows error 267でAI task開始前に失敗する問題を確認した。
- 選択pathは `--project` argumentだけへ渡し、process working directoryは `nullptr` とした。
- native ContractTestへfilesystem fileを選択pathとして実Invokeする267回帰試験を追加した。旧実装では失敗し、修正版は `IExplorerCommand.Invoke` PASSとなる。
- 旧 `SkillMagnet.ContextMenu_0.4.1.0` を `0.5.0.0`へ更新し、`menu_contract_matches_config: true`、`usable_installed_state: true`を確認した。
- installed DLLをCOM経由で実Invokeし、Skill Magnet実行確認画面が開くことを確認した。
- 修正後の実機画面: `docs/test-evidence/windows-error-267-fix-20260831/native-invoke-opened-skill-magnet-fixed.png`

## ローカル残留監査

次の3箇所を再帰走査し、`SKILL.md`、`acceptance.json`、`INDEX.md`、`.skill-magnet-snapshot.json`、`materialization.json`がすべて0件であることを確認した。

- `C:\Projects\skill-magnet`
- `C:\Users\HOMEA\.skill-magnet`
- `C:\Users\HOMEA\AppData\Local\Programs\Python\Python312\Lib\site-packages\skill_magnet`

`C:\Users\HOMEA\.skill-magnet`と実機証拠directoryにはJSON metadataが残る。これは利用者が許可したcontract/evidence情報であり、skill contentは含まない。

## Codex Desktop実機確認

- contract ID: `b5b31ec7b7cb4b9b88253999a56fafb0`
- attempt ID: `759e21b8a26a4c7cb8696298f83000a9`
- handoff status: `desktop_handoff_ready`
- prompt SHA-256: `ae9e32fcae5dc99e6f316a1b349a5e638e441f7a4dfebc3ed4d487070f313867`
- billing boundary: `existing_desktop_plan_no_api_key`
- prompt上部の投入確認: `docs/test-evidence/github-direct-skill-reference-20260830/codex-prompt-github-reference.png`
- GitHub固定commit URL表示確認: `docs/test-evidence/github-direct-skill-reference-20260830/codex-prompt-github-urls.png`
- handoff metadata: `docs/test-evidence/github-direct-skill-reference-20260830/handoff.json`

2枚目のスクリーンショットには、`raw.githubusercontent.com/omusubiman5/codex-pmo-skills/8f12af5.../<skill>/SKILL.md`とSHA-256がCodex Desktopの新規task入力欄へ入っている状態が写っている。自動送信は行っていない。

## 完了判定

- GitHub repositoryだけにskill contentを保管: PASS
- ローカルskill content 0件: PASS
- wheel/site-packagesへのskill同梱なし: PASS
- GitHub固定commit URLとdigestのCodex prompt投入: PASS
- 全自動テスト: PASS
- 実装計画書、実績報告書、実機スクリーンショット: 作成済み

## 2026-08-31 Windows実経路の最終E2E

- Windows File Explorerで `C:\Projects\skill-magnet` を実際に右クリックし、modern context menuの `Skill Magnet` → `Delivery Assurance` を選択してSkill Magnetの実画面を開いた。
- 実画面でAIにCodexを選び、依頼 `Skill Magnetの製品化チェックを実行し、重大な未完了事項を具体的に指摘してください。` を入力した。
- 起動確認ダイアログにpack、Codex、project、依頼全文が保持されていることを確認してhandoffを確定した。Windows error 267は再発せず、Skill Magnet画面は正常終了した。
- contract ID: `8119b643c2de4ae7b6d9375aff2e2116`
- attempt ID: `3f80fbfaa2054c04a22b4370e62fbec7`
- handoff status: `desktop_handoff_ready`
- prompt SHA-256: `c6614823868c353d994fa7b08aec820c59044976758227cb426e39b65e639dc9`
- installed 0.5.0 packageからpromptを再構成し、handoff記録のhash、actual request、INDEX URL、9件の固定commit SKILL.md URL、最低1件の実適用必須文言、読了だけを未完了とする文言がすべて一致した。
- Codex Desktop画面そのものは自動操作せず、製品仕様どおりhandoff受理までを記録した。LLM回答完了は主張しない。

## 2026-08-31 製品化チェック

- full suite: `138 tests PASS`、環境依存1件skip。
- Python compile: PASS。
- native build、署名、C++/Python COM contract: PASS。
- installed context menu: `usable_installed_state: true`、`menu_contract_matches_config: true`。
- `git diff --check`: whitespace errorなし。改行コードwarningのみ。
- ローカルskill content scan: project、state、installed packageのすべてで0件。
- macOS実機のFinder UX確認は別担当の試験項目であり、このWindows完了判定へ混入しない。
