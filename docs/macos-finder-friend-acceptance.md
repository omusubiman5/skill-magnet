# macOS Finder 友人実機受入

この手順は、macOSローカル導入版のFinder UIだけを確認する。Apple Developer ID、notarization、API key、従量課金API、追加支払いは不要。

## 対象

- release tag: `v0.5.2`
- Python: 3.12以上
- pack: `codex-delivery-assurance`
- 結果状態: `PENDING_EXTERNAL_FRIEND_TEST`

## 手順

```bash
git clone --branch v0.5.2 --depth 1 https://github.com/omusubiman5/skill-magnet.git
cd skill-magnet
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install .
python -m skill_magnet install-context-menu --platform macos --confirm
python -m skill_magnet context-menu-status --platform macos
```

1. Finderで任意のテストfolderを一つ選ぶ。
2. 右クリックまたはFinderの`Quick Actions`から`Skill Magnet`を選ぶ。
3. `Skill Magnet — 実行確認`が開くことを確認する。
4. projectが選択したfolder、packが`Delivery Assurance`であることを確認する。
5. `Cancel`で閉じる。CodexまたはClaudeへ依頼を送信しない。
6. 次を実行する。

```bash
python -m skill_magnet uninstall-context-menu --platform macos --confirm
test ! -e "$HOME/Library/Services/Skill Magnet.workflow"
```

## 合格記録

実施後、次を記入して結果状態を`PASS`または`FAIL`へ変更する。

| 項目 | 記録 |
|---|---|
| 実施日 | |
| macOS version | |
| Mac chip | |
| `git rev-parse HEAD` | |
| install status | |
| Finderメニュー表示 | |
| 正しいproject/pack表示 | |
| Cancelで外部送信なし | |
| uninstall後のworkflow残留なし | |
| 総合結果 | |

スクリーンショットを残す場合は、ユーザー名、home path、通知、他アプリなどの不要な個人情報を含めない。
