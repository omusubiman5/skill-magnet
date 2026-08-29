# Skill Magnet P1 復旧ベースライン台帳

作成日: 2026-08-28  
対象: `C:\Projects\skill-magnet`  
契約: `ORD-20260828-02`  
状態: P1 inventory。コード完成、テスト合格、実機受入、commit可否を宣言する文書ではない。

## 1. P1で固定した事実

- Git基準HEAD: `084173e6e4bb117f3f420f0f2a5331a4c7018457`
- branch / upstream: `main` / `origin/main`（`main...origin/main`、ahead/behind表示なし）
- main worktree: `C:/Projects/skill-magnet`、`refs/heads/main`
- 併存worktree: `C:/Users/HOMEA/.codex/worktrees/6c64/skill-magnet`、同一HEAD、detached
- 台帳作成前のdirty項目: staged 6、unstaged 11、untracked file 79
- 台帳作成後の追加項目: 本ファイル1件（untracked、テスト・文書、採用候補）
- indexとworktreeの変更は、停止確認・分類・本台帳追加以外には変更していない。reset、checkout、stash、commit、cleanup削除は実施していない。

このP1での復旧ベースラインは、上記HEADをGitアンカーとし、以下のtracked差分、untracked file一覧、主要SHA-256、untracked group manifest SHA-256を組み合わせた `REC-P1-20260828` とする。HEAD単体を検証対象へ戻したものではなく、現行dirty treeを失わず後続レビューできるよう固定したスナップショットである。

## 2. 残存実行の停止結果

停止直前に次の条件をすべて再確認した。

| PID | parent PID | image | 開始時刻 | 同一実行とした根拠 | 結果 |
|---:|---:|---|---|---|---|
| 17468 | 18520 | `python.exe` | 2026-08-27 23:39:19 | `C:\Projects\skill-magnet\src`、`run_module('skill_magnet')`、`context`、対象project/pack/skill/runtimeを含む | stopped |
| 18520 | 22924 | `cmd.exe` | 2026-08-27 23:39:19 | 上記python commandを直接起動 | stopped |
| 22924 | 23592 | `pwsh.exe` | 2026-08-27 23:39:18 | Skill Magnet Explorer leafのregistry commandを読み、上記cmdを起動 | stopped |

停止は子から親の順に対象3 PIDだけへ実施した。停止前に保護対象として採取した他のCodex、Computer Use、ChatGPT、VS Code、git-bash等78プロセスは停止後も78件すべて残存した。対象3 PIDは停止後0件である。Computer Use用 `node.exe` PID 8788、19068を含む非対象プロセスへ停止操作は行っていない。

最終照合時、数値PID 18520は開始時刻2026-08-28 01:32:19、親PID 13636、`cua_node/.../node.exe ./server.mjs` として再利用されていた。これは停止した2026-08-27 23:39:19開始の `cmd.exe` とは別プロセスであるため停止していない。全プロセスをcommand lineで再検索し、`C:\Projects\skill-magnet` を対象とする `context` 実行は0件であることを確認した。以後、数値PIDの一致だけを残存判定に使わない。

## 3. 分類ルール

| 区分 | 意味 | P1での扱い |
|---|---|---|
| 製品コード | runtime動作・policy・repository設定へ影響する差分 | 採用候補または保留。P1では未採用 |
| テスト・文書 | test、integration gate、README、設計・結果・調査文書 | 採用候補または保留。主張とコードの整合をP2で確認 |
| 保存対象証拠 | lifecycle、contract、evidence、registry readback、debug text | 保留し保存。完成証拠への昇格は禁止 |
| 生成物・一時物 | `.obj/.lib/.exp/.exe/.dll`、build `out`、debug package snapshot | 採用対象外の生成物。P1では削除しない |

`採用候補` はcommit承認ではない。目的に関連するためP2の差分レビュー対象に含める、という意味だけである。`保留` は所有者・意図・代替関係が未確定で、採用・復元・削除のいずれもP1では行わない。

## 4. tracked inventory

### 4.1 staged 6件

staged stat: 6 files changed, 639 deletions。

| status | path | 分類 | 判定 | 根拠 |
|---|---|---|---|---|
| D | `.beads/.gitignore` | 製品外repository metadata | 保留 | Beads/Dolt運用削除。製品復旧との関連とowner未確認 |
| D | `.beads/README.md` | 製品外repository metadata | 保留 | 同上 |
| D | `.beads/config.yaml` | 製品外repository metadata | 保留 | 同上 |
| D | `.beads/metadata.json` | 製品外repository metadata | 保留 | 同上 |
| D | `integration/explorer_results_gate.py` | テスト・文書（integration gate） | 保留 | 旧results gate廃止の意図と代替gate未確認 |
| D | `tests/test_results_gate.py` | テスト・文書（test） | 保留 | 上記integration gateと対になる削除。P2で同時判断が必要 |

### 4.2 unstaged 11件

unstaged stat: 11 files changed, 540 insertions, 175 deletions。Gitは全11件に「次回Gitが触れるとLFがCRLFへ置換される」warningを表示したため、P2でline-ending由来差分の混入を避ける。

| status | path | 分類 | 判定 | 根拠 |
|---|---|---|---|---|
| M | `.gitignore` | 製品外repository設定 | 保留 | Beads/Dolt ignore 8行の削除であり、staged `.beads` 削除と一体。製品修正とは分離判断が必要 |
| M | `README.md` | テスト・文書 | 採用候補 | 完了状態を `verified_applied` からactual requestに結び付く `verified_completed` へ修正 |
| M | `docs/mvp-redesign.md` | テスト・文書 | 採用候補 | completion evidence要件を設計へ反映 |
| M | `docs/windows-explorer-leaf-launch-results.md` | テスト・文書 | 採用候補 | 過去PASSの撤回、未完了gate、automated evidence限定を記録 |
| M | `policy/product-policy.json` | 製品コード（policy） | 採用候補 | completion evidence keyへ変更 |
| M | `src/skill_magnet/activation.py` | 製品コード | 採用候補 | actual request hash、非空deliverable、completed skill IDs/status、`verified_completed`を強制 |
| M | `src/skill_magnet/cli.py` | 製品コード | 採用候補 | Explorer contextで依頼選択後にinteractive handoffを実行し、error surfaceを保持 |
| M | `src/skill_magnet/platforms.py` | 製品コード | 採用候補 | 可視`python.exe` leaf、menu平坦化、modern install status/transaction、web transportを変更 |
| M | `src/skill_magnet/ui.py` | 製品コード | 採用候補 | actual request入力・確認とエラー表示を変更 |
| M | `tests/test_activation.py` | テスト・文書（test） | 採用候補 | completion contract、leaf、installer lifecycle等の回帰testを追加・更新 |
| M | `tests/test_product_policy.py` | テスト・文書（test） | 採用候補 | policy key変更へ追従 |

### 4.3 HEADに対するtracked合計

`git diff HEAD --stat`: 17 files changed, 540 insertions, 814 deletions。

## 5. untracked inventory

### 5.1 テスト・文書 — 採用候補 3件（台帳作成前2件 + 本台帳）

| path | 判定 | 根拠 |
|---|---|---|
| `docs/unimplemented-user-task-root-cause.md` | 採用候補 | 実機未確認、Computer Use失敗、原因仮説と証拠基準を明示 |
| `docs/user-task-application-fix-report.md` | 採用候補 | 対応を未完了として記録し、過去自動testと実動受入を分離 |
| `docs/recovery-inventory-2026-08-28.md` | 採用候補 | ORD-20260828-02で新規作成した本P1台帳。自己hashは記録しない |

台帳作成前2件のgroup manifest SHA-256（`path<TAB>file_sha256`をpath昇順、LF結合）: `4309cd147654897a34e915cc95d95b1fc750e2915d7f7f0d98ad3dd5fd302a66`。

### 5.2 保存対象証拠 — 保留 61件

これらは過去attemptの事実追跡に必要なため保存する。ただし、自動生成JSON、registry export、debug textだけでExplorer実動または製品完成を主張しない。

Group facts: 61 files、75,435 bytes、mtime 2026-08-23 02:18:04〜2026-08-27 22:52:42。manifest SHA-256: `c06127c2ce585ec22f4ef64c86af0566d8e19c0015f7fcdd0b58278f22eecba8`。

```text
.e2e-state/actual-request-evidence-20260827/events/7f238281fb92437485ba2d3d73bff2aa-lifecycle.jsonl
.e2e-state/actual-request-evidence-20260827/evidence/7f238281fb92437485ba2d3d73bff2aa-verified.json
.e2e-state/actual-request-evidence-20260827/launch-contracts/7f238281fb92437485ba2d3d73bff2aa.json
.e2e-state/installed-ui-fix-backup-20260823/Background.reg
.e2e-state/installed-ui-fix-backup-20260823/Directory.reg
.e2e-state/ps-debug.txt
.e2e-state/ps-debug2.txt
.e2e-state/skill-completion-evidence-20260827/events/6136fecaae024c57ab0f78ffe81ef7b9-lifecycle.jsonl
.e2e-state/skill-completion-evidence-20260827/evidence/6136fecaae024c57ab0f78ffe81ef7b9-verified.json
.e2e-state/skill-completion-evidence-20260827/launch-contracts/6136fecaae024c57ab0f78ffe81ef7b9.json
.e2e-state/sm-62a.6.7-modern-ui-final-readback/Background.reg
.e2e-state/sm-62a.6.7-modern-ui-final-readback/Directory.reg
.e2e-state/sm-audit-rework-claude-final3/events/d3c52a9dc8a44b11b40613f1222711c8-rejected.json
.e2e-state/sm-audit-rework-drift-final2/events/9450451905af4273a6e6e1729d683698-rejected.json
.e2e-state/sm-int-002-evidence/events/a868c753a1b94b4f86d85f21bc5583fb-lifecycle.jsonl
.e2e-state/sm-int-002-evidence/evidence/a868c753a1b94b4f86d85f21bc5583fb-not-guaranteed.json
.e2e-state/sm-int-002-evidence/launch-contracts/a868c753a1b94b4f86d85f21bc5583fb.json
.e2e-state/sm-int-002-final/events/c8b97752a0ad49b2ab64cfc70e97b52c-lifecycle.jsonl
.e2e-state/sm-int-002-final/evidence/c8b97752a0ad49b2ab64cfc70e97b52c-verified.json
.e2e-state/sm-int-002-final/launch-contracts/c8b97752a0ad49b2ab64cfc70e97b52c.json
.e2e-state/sm-int-002-verified/events/2d072a7a58f74eb88fc602f8835e22a5-lifecycle.jsonl
.e2e-state/sm-int-002-verified/evidence/2d072a7a58f74eb88fc602f8835e22a5-not-guaranteed.json
.e2e-state/sm-int-002-verified/launch-contracts/2d072a7a58f74eb88fc602f8835e22a5.json
.e2e-state/sm-int-003-final/events/de47cd747f8a4f17aa25af40549913df-lifecycle.jsonl
.e2e-state/sm-int-003-final/evidence/de47cd747f8a4f17aa25af40549913df-verified.json
.e2e-state/sm-int-003-final/launch-contracts/de47cd747f8a4f17aa25af40549913df.json
.e2e-state/sm-int-004-final/events/254b6291a0ba42f6b8c3cd979c459857-rejected.json
.e2e-state/sm-int-004-final/events/79629da7c6334c78ba3ba33fbf15ce3b-rejected.json
.e2e-state/sm-int-004-final/events/bc6d36b701094e39a413a016f132774c-rejected.json
.e2e-state/sm-int-004-final/events/eedcf40be80144b9b6a9512d512f8f5d-rejected.json
.e2e-state/sm-int-005-final/events/d188370d527447c08843aa68ba82e07a-lifecycle.jsonl
.e2e-state/sm-int-005-final/evidence/d188370d527447c08843aa68ba82e07a-not-guaranteed.json
.e2e-state/sm-int-005-final/launch-contracts/d188370d527447c08843aa68ba82e07a.json
.e2e-state/sm-int-006-final/events/3c19bc060ab14fd68944b5172a048799-rejected.json
.e2e-state/sm-sk-001-evidence/events/899d38fc86464b71801c8368bb841c95-lifecycle.jsonl
.e2e-state/sm-sk-001-evidence/evidence/899d38fc86464b71801c8368bb841c95-verified.json
.e2e-state/sm-sk-001-evidence/launch-contracts/899d38fc86464b71801c8368bb841c95.json
.e2e-state/sm-sk-002-evidence/events/2ea3a004963b4dc0a76f040a2ec906ae-lifecycle.jsonl
.e2e-state/sm-sk-002-evidence/evidence/2ea3a004963b4dc0a76f040a2ec906ae-verified.json
.e2e-state/sm-sk-002-evidence/launch-contracts/2ea3a004963b4dc0a76f040a2ec906ae.json
.e2e-state/sm-sk-003-evidence/events/f076e117d5d4429f9ea4e29a9a101f42-lifecycle.jsonl
.e2e-state/sm-sk-003-evidence/evidence/f076e117d5d4429f9ea4e29a9a101f42-verified.json
.e2e-state/sm-sk-003-evidence/launch-contracts/f076e117d5d4429f9ea4e29a9a101f42.json
.e2e-state/sm-sk-004-evidence/events/7432986735e14632af1f908c5cbf7e6e-lifecycle.jsonl
.e2e-state/sm-sk-004-evidence/evidence/7432986735e14632af1f908c5cbf7e6e-verified.json
.e2e-state/sm-sk-004-evidence/launch-contracts/7432986735e14632af1f908c5cbf7e6e.json
.e2e-state/sm-sk-005-evidence/events/fc1a4c99eddb42f0b43bcf224815fa93-lifecycle.jsonl
.e2e-state/sm-sk-005-evidence/evidence/fc1a4c99eddb42f0b43bcf224815fa93-verified.json
.e2e-state/sm-sk-005-evidence/launch-contracts/fc1a4c99eddb42f0b43bcf224815fa93.json
.e2e-state/sm-sk-006-evidence/events/cdf0d2fde40a4845b60fdd9d7cb3d271-lifecycle.jsonl
.e2e-state/sm-sk-006-evidence/evidence/cdf0d2fde40a4845b60fdd9d7cb3d271-verified.json
.e2e-state/sm-sk-006-evidence/launch-contracts/cdf0d2fde40a4845b60fdd9d7cb3d271.json
.e2e-state/sm-sk-007-evidence/events/47620c23a0df4ff0bf6020f8e845e921-lifecycle.jsonl
.e2e-state/sm-sk-007-evidence/evidence/47620c23a0df4ff0bf6020f8e845e921-verified.json
.e2e-state/sm-sk-007-evidence/launch-contracts/47620c23a0df4ff0bf6020f8e845e921.json
.e2e-state/sm-sk-008-evidence/events/6bc34a2262a74070a9604e3eef5eefa2-lifecycle.jsonl
.e2e-state/sm-sk-008-evidence/evidence/6bc34a2262a74070a9604e3eef5eefa2-verified.json
.e2e-state/sm-sk-008-evidence/launch-contracts/6bc34a2262a74070a9604e3eef5eefa2.json
.e2e-state/sm-sk-009-evidence/events/498e2d8b946141649915c3db5362a7ec-lifecycle.jsonl
.e2e-state/sm-sk-009-evidence/evidence/498e2d8b946141649915c3db5362a7ec-verified.json
.e2e-state/sm-sk-009-evidence/launch-contracts/498e2d8b946141649915c3db5362a7ec.json
```

### 5.3 生成物・一時物 — 採用対象外 16件

いずれもP1では削除しない。

| group | files | bytes | mtime範囲 | manifest SHA-256 | paths |
|---|---:|---:|---|---|---|
| debug build snapshot | 7 | 300,788 | 2026-08-23 10:54:07〜11:00:27 | `d81907cb0c8330e5787f002410763a21fd554e93f6915b98825ee5dcc2822159` | 下記7件 |
| root build products | 5 | 653,460 | 2026-08-23 10:48:35〜2026-08-28 00:01:53 | `1490e891a3ed562d98dc0f5ddb96226ec18ff7f84c1e11d4e4514660b9fd11a8` | 下記5件 |
| native out products | 4 | 519,422 | 2026-08-23 11:23:22〜2026-08-28 00:10:18 | `79687ab61497f5377145387d160601cec0ae2cb506ba288e0aef057cfbc85c39` | 下記4件 |

```text
.e2e-state/modern-debug/AppxManifest.xml
.e2e-state/modern-debug/Assets/Square150x150Logo.png
.e2e-state/modern-debug/Assets/Square44x44Logo.png
.e2e-state/modern-debug/Assets/StoreLogo.png
.e2e-state/modern-debug/SkillMagnetCommand.dll
.e2e-state/modern-debug/SkillMagnetLauncher.exe
.e2e-state/modern-debug/SkillMagnetMenu.tsv
ContractTest.obj
SkillMagnetCommand.exp
SkillMagnetCommand.lib
SkillMagnetCommand.obj
SkillMagnetLauncher.obj
native/windows-modern-context-menu/out/ContractTest.exe
native/windows-modern-context-menu/out/SkillMagnetCommand.dll
native/windows-modern-context-menu/out/SkillMagnetLauncher.exe
native/windows-modern-context-menu/out/SkillMagnetMenu.tsv
```

## 6. 主要ファイルhash

現存ファイルはSHA-256。staged削除ファイルは内容がworktreeに存在しないためHEAD Git blob IDを記録する。

| path | hash |
|---|---|
| `.gitignore` | SHA-256 `fa05bb8b71bffbd98663b149955faf6b7dc9565470349ea93fe63f09108b29aa` |
| `README.md` | SHA-256 `950a9884b91fc8c0636e3053a90832daaacc6c51fd8b052b805148df83fc9425` |
| `docs/mvp-redesign.md` | SHA-256 `8201616ef8e5842306fba61a4e6a341f0418391470b98d4ee0ae17395df25653` |
| `docs/windows-explorer-leaf-launch-results.md` | SHA-256 `4b622e88bad262dfe94b6e091a509f4f0c2d6f0632337fa4082ebc16260ec21e` |
| `docs/unimplemented-user-task-root-cause.md` | SHA-256 `04fb910e2a9244ba6178d176a80aa24a9f48b8071abfaa473153cbcfb2c88999` |
| `docs/user-task-application-fix-report.md` | SHA-256 `32cffd4b7a186e79a7b77d3544a4041bf69e55952e2d94d6958e410ec1081d54` |
| `policy/product-policy.json` | SHA-256 `b90770fbd7bbf8985376dea10c615a773f695379867a977e99a3d17ac7e14bbe` |
| `src/skill_magnet/activation.py` | SHA-256 `96870c1f38cb4bd36f438f560d72a400b79d45867ae261fe72d355ddf65546f0` |
| `src/skill_magnet/cli.py` | SHA-256 `e52513dea4e2d38e5f5d06e5d207d5256c9b768aefab53f7fe8bb1cde6e15c58` |
| `src/skill_magnet/platforms.py` | SHA-256 `9d2528e078e4032819659377a4f23b29d632979c2ee59c0d8907ec2ea67fc3db` |
| `src/skill_magnet/ui.py` | SHA-256 `0fee982a8a6b901c463597688b4d600cbe19106e62402ae6432a371233a8531a` |
| `tests/test_activation.py` | SHA-256 `cbec5a9ec86b7d04336f0a7d24398d4a7fb01c96857e158c70c27eadea6bb3c4` |
| `tests/test_product_policy.py` | SHA-256 `d695704af943beaa592193bf721b621a3e050716cb7604e345d64a695548df35` |
| `.beads/.gitignore` | HEAD blob `7ba2a936d3ea907755cfba2c00bb328d63d186d5` |
| `.beads/README.md` | HEAD blob `63e8f4c232ee83a6a239ae43b85f9c7903601dbb` |
| `.beads/config.yaml` | HEAD blob `afb570ac8aaf2300dd4a2e27f4834c9ae747017d` |
| `.beads/metadata.json` | HEAD blob `b494d43c6ef73417cce3d13c863efabc989a3582` |
| `integration/explorer_results_gate.py` | HEAD blob `07464174ac6f22a53d0b0f1b77aae201a40011d8` |
| `tests/test_results_gate.py` | HEAD blob `579d972ff376fed498110f886d9d3fa4d9d26050` |

## 7. P2への境界

- P2の差分レビュー対象候補は、unstagedの製品コード5件、テスト2件、README/設計/結果文書3件、untracked調査・対応報告2件である。
- `.gitignore`とstaged `.beads` 4削除は同一のrepository tooling変更として保留する。
- stagedのintegration gate/test 2削除は、代替gateと削除理由を確認できるまで保留する。
- 保存対象証拠61件は製品差分へ混ぜず、attempt IDと主張の対応を後続で監査する。
- 生成物16件は採用対象外だが、cleanup承認がないため現存のまま保全する。
- 本P1ではtest、Explorer操作、Computer Use、認証、install、commitを実施していない。P2へは自動進行しない。

## 8. P1検証コマンド

実行したのは状態取得、hash計算、対象PID停止と停止後照合だけである。

```text
Get-CimInstance Win32_Process
Stop-Process -Id 17468 -Force
Stop-Process -Id 18520 -Force
Stop-Process -Id 22924 -Force
git rev-parse HEAD
git symbolic-ref --short -q HEAD
git rev-parse --abbrev-ref --symbolic-full-name @{u}
git status --short --branch
git worktree list --porcelain
git diff --cached --name-status
git diff --name-status
git diff --cached --stat
git diff HEAD --stat
git ls-files --others --exclude-standard
Get-FileHash -Algorithm SHA256
```

P1終了時の判定: 対象残存実行停止 `met`、非対象プロセス保全 `met`、dirty全項目分類 `met`、HEAD/worktree/stat/hash記録 `met`、P2未着手 `met`。
