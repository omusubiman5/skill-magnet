# Skill Magnet P2 復旧検証結果

作成日: 2026-08-28  
契約: `ORD-20260828-03`  
基準: `REC-P1-20260828`  
対象: Codex actual request → launch contract → completion evidence → `verified_completed`

## 1. 判定

P2の自動検証は **PASS**。

actual request、選択skill ID、completed status、actual-request SHA-256、非空task output、skill固有acceptanceがコード・policy・testで一致し、focused testと現存full suiteがPASSした。これは自動検証可能なCodex契約の判定であり、Explorer実機、install状態、外部Codex/Claude、認証、製品完成を証明しない。

## 2. 契約の対応

| 要件 | code | policy | test / 判定 |
|---|---|---|---|
| actual request | `LaunchContract.purpose`を`PURPOSE`へ送り、SHA-256をPROVENANCEへ固定 | `completion_contract.actual_request_field=contract.purpose` | 成功E2Eでactual requestが`result.task_output`へ残ることを確認 |
| 選択skill ID | `evidence.completed_skill_ids == contract.skill_ids`を完全一致検証 | `selected_skill_ids_field=evidence.completed_skill_ids` | 欠落・不一致を否定test |
| completed status | `evidence.skill_execution_status == completed` | required status `completed` | `pending`を否定test |
| actual-request SHA-256 | `sha256(contract.purpose UTF-8)`と`evidence.actual_request_sha256`を一致検証 | fieldを明示し、Codex inject requiredへ追加 | 異なるSHA-256を否定test |
| 非空task output | `result.task_output`がstringかつ`strip()`後非空 | fieldとnon-empty要件を明示 | whitespace-onlyを否定test |
| skill固有acceptance | 全`acceptance.json` assertionをoutputへ実行 | `skill_specific_acceptance_must_pass=true` | assertion mismatchを否定test |
| applied rule identity | 各ruleは正確な`<skill-id>:` prefixを必須化 | completion evidenceとskill acceptanceを自己申告だけにしない | 文字列中にskill IDを含むだけのclaimを否定test |
| terminal success | 全条件通過後だけ`verified_completed`を発行 | success requires delivery/read/completion evidence | lifecycleに早期successがないことを確認 |

`actual_request_sha256`はtask envelopeで送達されるが、検証結果ではskill-read evidenceとcompletion evidenceを分離し、後者だけへ収録する。

## 3. P2で行った最小修正

- `src/skill_magnet/activation.py`
  - applied ruleを正確なskill ID prefixで照合。
  - skill-read evidenceからactual-request SHA-256を分離し、completion evidenceとして検証・記録。
- `policy/product-policy.json`
  - completion契約の各field、required completed status、non-empty task output、skill acceptance必須を明文化。
  - Codex task injection必須項目へactual-request SHA-256を追加。
- `src/skill_magnet/platforms.py`
  - full suiteで判明したrollback欠陥を修正。現行`SkillMagnetClassic` 2 rootだけでなく、install時に削除するlegacy `SkillMagnet` 2 rootも開始前backupへ含め、同じ4 owned rootを復元対象にした。
  - 既存version 1 rollback metadataの読み取り互換を維持。
- `tests/test_activation.py`
  - completion claim 6否定ケースを追加。
  - current classic root、flattened leaf label、legacy migration、4-root backup/rollback契約へ既存testを整合。
- `tests/test_product_policy.py`
  - completion契約とinject必須項目を固定。

READMEと対象設計・結果・調査文書はレビューし、`verified_applied`の残存表記は過去履歴または旧欠陥の説明であることを確認した。P2で追加修正はしていない。

## 4. 実行したtest

### Focused

```text
py -3.12 -m unittest -v \
  tests.test_activation.ActivationEndToEndTest.test_cross_platform_manual_selection_to_verified_application_e2e \
  tests.test_activation.ActivationEndToEndTest.test_missing_skill_completion_evidence_never_reaches_terminal_success \
  tests.test_activation.ActivationEndToEndTest.test_completion_contract_rejects_each_mismatched_claim \
  tests.test_activation.ActivationEndToEndTest.test_windows_context_collects_actual_request_before_execution \
  tests.test_product_policy
```

結果: `Ran 12 tests in 4.045s`、`OK`。

### Full suite

```text
py -3.12 -m unittest discover -s tests -v
```

初回結果: `Ran 98 tests in 112.851s`、9 failures、1 skipped。completion契約testはPASS。失敗は、classic root testの旧期待値と、legacy rootをbackupしないtransaction欠陥へ集約された。

最小修正後の最終結果: `Ran 98 tests in 129.313s`、`OK (skipped=1)`。

skipは `test_pythonw_entrypoint_hard_exits_after_failure_ui_returns` の1件で、理由は現行Python installationに`pythonw.exe`がないため。現行製品差分はExplorer leafを`python.exe`へ変更しているが、このtestは旧windowless child regression用として残っている。

`tests/test_results_gate.py` はP1からstaged削除保留中でworktreeに存在しない。したがって上記discovery対象外であり、復元・変更・削除確定はしていない。現存test modulesは `test_activation.py`、`test_e2e_guard.py`、`test_mvp.py`、`test_product_policy.py`。

### Diff check

```text
git diff --check
```

結果: exit `0`。whitespace errorなし。既存11 tracked worktree fileについて「Gitが次回触れるとLFをCRLFへ置換する」warningだけを表示し、errorとは区別した。

## 5. 検証済みGit・dirty manifest

検証採取時刻: 2026-08-28 01:44:15 +09:00  
Python: 3.12.10

| 項目 | 値 |
|---|---|
| HEAD | `084173e6e4bb117f3f420f0f2a5331a4c7018457` |
| branch / upstream | `main` / `origin/main` |
| staged | 6（P1からの削除保留） |
| unstaged | 11 |
| untracked | 80 |
| status entries | 97 |
| tested dirty manifest SHA-256 | `576a6fca67767eb04fc8d9af4bd47e25b87735f3325a6c4956afa5d87672128b` |

Manifest生成規則:

1. `git -c core.quotepath=false status --porcelain=v1 --untracked-files=all` の各entryを使用。
2. 現存fileはSHA-256、staged削除は`HEAD:<path>`のGit blob IDを付与。
3. `status<TAB>path<TAB>hash-kind:hash`をpathを含む全行でsortし、LF結合したUTF-8 bytesをSHA-256化。

このmanifestはtestと`git diff --check`を実行したtreeを表す。本P2結果文書は検証後に作成したためmanifestへ含めない。文書追加後はuntrackedが81になるが、製品source/test bytesは下記hashから変えていない。

Tracked diff stat at validation:

- unstaged: 11 files changed, 730 insertions, 194 deletions
- staged: 6 files changed, 639 deletions

## 6. 主要SHA-256

| path | SHA-256 |
|---|---|
| `policy/product-policy.json` | `831195c6078abfc9e9a2894e816f4a8cf8c587096b587c92c66b523ba52e10f4` |
| `src/skill_magnet/activation.py` | `45f44d6712958e006ea893a70953edacc7b0b65d419cec64c71ff46330c1023b` |
| `src/skill_magnet/cli.py` | `e52513dea4e2d38e5f5d06e5d207d5256c9b768aefab53f7fe8bb1cde6e15c58` |
| `src/skill_magnet/platforms.py` | `748fa836126e1ec8eec4e6b47887495cb4fab755c782f59bafdee2fe07090627` |
| `src/skill_magnet/ui.py` | `0fee982a8a6b901c463597688b4d600cbe19106e62402ae6432a371233a8531a` |
| `tests/test_activation.py` | `f91dea71b37d24f5a0716d2c4a0c3a986a53fa6bba77540efdf9ec00c27feaa4` |
| `tests/test_product_policy.py` | `ecca0fb993a3aea764f0a232477c901164abc65614e84d163bc29164d3d00011` |
| `README.md` | `950a9884b91fc8c0636e3053a90832daaacc6c51fd8b052b805148df83fc9425` |
| `docs/mvp-redesign.md` | `8201616ef8e5842306fba61a4e6a341f0418391470b98d4ee0ae17395df25653` |
| `docs/windows-explorer-leaf-launch-results.md` | `4b622e88bad262dfe94b6e091a509f4f0c2d6f0632337fa4082ebc16260ec21e` |
| `docs/unimplemented-user-task-root-cause.md` | `04fb910e2a9244ba6178d176a80aa24a9f48b8071abfaa473153cbcfb2c88999` |
| `docs/user-task-application-fix-report.md` | `32cffd4b7a186e79a7b77d3544a4041bf69e55952e2d94d6958e410ec1081d54` |
| `docs/recovery-inventory-2026-08-28.md` | `64534687cfe0a6fdcbf0b39909f41fec85197d7132e83a345ef5c25514073090` |

## 7. 境界と次の再開条件

- `.beads`、`.gitignore`、`integration/explorer_results_gate.py`、`tests/test_results_gate.py`は変更・復元・削除確定していない。
- 生成物・保存対象証拠のcleanup、commit、stash、resetは行っていない。
- Explorer実機、install、Computer Use、Claude、認証、外部操作は行っていない。
- P2は完了。P3へは自動進行しない。
