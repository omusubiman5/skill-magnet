# Skill Magnet fixed-commit review

## 状態

- Beads gate: `sm-62a.2`（Option A 承認済み、実行証拠PASS）
- pack: `codex-pmo-skills`（9 skills）
- config `expected_commit`: `c7747bba0bc391316aa558b3b4e8dd412045d2dc`
- approved snapshot: `C:\Projects\skill-magnet\.approved-snapshots\codex-pmo-skills-c7747bba`
- snapshot `HEAD`: `c7747bba0bc391316aa558b3b4e8dd412045d2dc`
- snapshot worktree: clean
- origin: `https://github.com/omusubiman5/codex-pmo-skills.git`
- original source: 非変更。HEAD/checkout、dirty運用artifact、remoteをそのまま保持

ユーザーはOption Aを明示承認。`expected_commit`とdigestを変えず、configのsource pathだけを独立clean snapshotへ向けた。menu reinstallと実Codex起動は後続Issueの受入条件に従う。

## Option A 実行証拠

- local independent clone: `git clone --no-checkout --no-local` → detached `c7747bba...`
- snapshot owner/origin: `omusubiman5` / `https://github.com/omusubiman5/codex-pmo-skills.git`
- snapshot status: clean、HEADは40桁承認SHAと完全一致
- product config: `expected_commit`/approval/9 skill IDsは不変、source pathだけをsnapshotへ更新
- verification: 9 skillのinstruction/acceptance SHA-256全18値が既定値と一致、18 menu leafを生成
- test: `test_product_menu_has_all_nine_individual_skills_and_no_pack_leaf` PASS
- original source: cleanup/commit/push/checkout/config変更なし

## 差分の要約

`c7747bba... → 225acbe...` は184 additions / 21 deletions、8 filesです。

- 追加: `codex-pmo-orchestration/` の `SKILL.md`、`acceptance.json`、`test-prompts.json`、`test-results.md`
- 更新: `README.md`、`DELIVERY_MANIFEST.md`、`DISTRIBUTION.md`、`RELEASE_NOTES.md`
- 既存pack対象9 skill directories: 変更なし
- 既存9 skillのinstruction SHA-256、acceptance SHA-256、acceptance assertion: `individual-skill-test-plan.md` の固定値から変更なし
- 新しい `codex-pmo-orchestration` は現在の `skill-magnet.json` の9-skill packには含まれない

したがってsource commitは不一致ですが、現在の9件のskill bytes自体は同一です。ただしSkill Magnetの安全契約はrepository commit全体を固定するため、bytesが同じという理由だけで `225acbe...` を自動承認しません。

## 選択肢

### A. 既承認 `c7747bba...` を維持する（9-skillテスト計画に最小）

- config SHA、9 skill digest、acceptance、結果行列を変更しない。
- 現在のmain worktreeを動かさず、別のclean checkout/worktreeを `c7747bba...` に用意してSkill Magnet sourceとして使う。
- 個別skill menu実装後にmenu reinstallし、leafのcommit/9 skill IDs/digestsを再検証する。
- `SM-SK-001`〜`009` を計画どおり実行できる。

影響: 新しい `codex-pmo-orchestration` はこのpack/行列には現れません。

### B. `225acbe...` を新しいsource commitとして承認し、packは9 skillsのままにする

- 追加PMO skillとrepository文書差分をユーザーがレビュー・承認する。
- config `expected_commit`、approval timestamp/reference、repository-level provenanceを更新する。
- 9 skill bytesは同一なので各instruction/acceptance digestは不変であることを再計算して記録する。
- 新PMO skillは明示的にpack対象外とし、menuに表示しない。
- menu reinstall後にcommit、9 skill IDs、digests、clean HEADを再検証する。

影響: source commitは最新になりますが、未選択の10件目がrepositoryに存在する境界をcontract/testで明示する必要があります。

### C. `225acbe...` を承認し、PMO skillもpackへ追加する

現在の9個別skillテスト計画の範囲外です。10件目のTest ID、instruction/acceptance digest、複数assertion、Beads実行Issue、結果行、独立計画監査が必要です。この決定をA/Bと同時に暗黙実行しません。

## 推奨

現在のユーザー要件が「9個の個別skillを一件ずつテスト・利用」である間はAが最小です。main sourceを巻き戻さず、承認済みcommitの別clean checkoutを用いるため、新PMO skillの作業も失いません。

## 決定後のゲート

1. ユーザー決定を `sm-62a.2` と結果MDへ記録する。
2. 選択したcommitのowner/origin/clean HEADを検証する。
3. 9件のinstruction/acceptance SHA-256とassertionを再計算し、テスト計画と照合する。
4. Aならconfig SHAを変えずsource pathだけを承認済みcheckoutへ向ける変更案を提示する。Bならconfig SHA/approval/provenanceの差分を提示する。
5. ユーザー承認後にだけconfigを変更する。
6. 個別skill menu実装完了後にmenu reinstallし、leafのcommit/skill/digestを照合する。
7. `sm-62a.5.1`以降と9個別skill行列へ進む。

## 読み取り証拠

- `git log c7747bba..225acbe --oneline`: `225acbe feat: add operational PMO orchestration skill`
- `git diff --name-status c7747bba 225acbe`: 既存9 skill directoryの変更なし
- `git status --short`: source clean
- fixed blob SHA-256再計算: `individual-skill-test-plan.md` の全18 digestと一致
