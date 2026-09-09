# Windows Explorer — 現在候補の受入記録

対象: ソース `24b5d8717db5ba1d46e75b82947c19a70f5781e5`、Windows 0.5.9、ローカル署名版。
過去の419件・旧wheel・旧実機証拠は[履歴](windows-explorer-leaf-launch-results-history-2026-09-09.md)へ分離しました。

## 集約記録

<!-- explorer-results-ledger:start
{
  "release_scope": "direct-root-unified-selector",
  "release_version": "0.5.9",
  "distribution_scope": "local-self-signed",
  "full_test_count": 437,
  "menu_leaf_count": 0,
  "menu_action_count": 1,
  "root_launcher_entry_count": 1,
  "configured_selection_count": 3,
  "library_manager_entry_count": 0,
  "register_folder_entry_count": 0,
  "selection_kinds": [
    "package",
    "skill"
  ],
  "pack_skill_counts": [
    1,
    9,
    12
  ],
  "release_code_sha": "24b5d8717db5ba1d46e75b82947c19a70f5781e5",
  "wheel_payload_sha256": "f0fda158206474f4a4444ac8f6f6cd78a3bb6e4282b0632da12ef9b27d4fedb9",
  "automated_status": "LOCAL_RELEASE_GATE_PASS_437",
  "windows_explorer_field_status": "PASS_REAL_EXPLORER_DIRECT_ROOT_INVOKE_0_5_9",
  "windows_explorer_field_invoke_log_sha256": "68566acb9496764dc880295d345e7d4112bf5606f081d3f5eed33d5706082878",
  "windows_explorer_field_bundle_sha256": "fc907bed210ea865063d4b39a68c246f2bf1a5ff6c9aa6da085f6aa7a63e4dea",
  "windows_explorer_field_signer_thumbprint": "4fda581516d0b016ed7db4c97c1e033f2d50c3f9",
  "macos_finder_field_status": "CI_SEMANTIC_ONLY_REAL_UI_NOT_CLAIMED_FOR_0_5_2",
  "public_distribution_status": "NOT_CLAIMED_REQUIRES_EXTERNAL_PUBLISHER",
  "codex_desktop_result_status": "HANDOFF_READY_ANSWER_COMPLETION_NOT_CLAIMED"
}
explorer-results-ledger:end -->

- 統合テスト: 現行suite — 437 tests PASS を受入基準とする。実行結果・スキップ理由は[対応報告](fix-report-structural-drift-2026-09-09.md)に記録する。
- 実機証拠: [署名付きbundle](evidence/windows-explorer-direct-root-0.5.9.json)、[native invoke記録](evidence/windows-explorer-direct-root-0.5.9.log)。採取日時: `2026-09-09T07:53:25.083Z`。
- Explorerの単一入口から共通画面を開き、管理画面、登録時の不足ファイル案内、選択フォルダー・背景、同一フォルダーの再投入、別フォルダーの競合、閉じた後の再起動を確認した。
- 同じOS登録のまま、本文・commit更新、パック追加・削除を公開から有効化まで通す回帰試験を追加した。
- source、wheel、導入Python、native DLLの一致を検証した。旧候補の成功結果を今回の証拠へ転用していない。

## 判定範囲

Windows MVPの受入記録であり、macOS実機・公開配布・AIタスクの回答完成を認定するものではない。
検証条件は `integration/explorer_results_gate.py` とGitHub Actionsで維持する。
詳細: [原因調査](cause-investigation-structural-drift-2026-09-09.md)、[対応報告](fix-report-structural-drift-2026-09-09.md)。
