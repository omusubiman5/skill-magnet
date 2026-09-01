# macOSローカル導入版ポリシー

決定日: 2026-09-01

## 決定

Skill MagnetのmacOS版は、署名済み`.app`やinstaller packageとして一般配布せず、Python 3.12以上で導入するライブラリとFinder Quick Actionとして提供する。

この配布形態では、Apple Developer ID署名、Apple notarization、Mac App Store公開をリリース要件にしない。利用者はGitHubの固定tagまたは検証済みwheelからローカル環境へ導入し、Skill Magnet自身のCLIでFinder Quick Actionを登録・解除する。

## 対象範囲

- Python package / wheel
- `~/Library/Services/Skill Magnet.workflow`へ登録するFinder Quick Action
- Finderで選んだdirectoryをSkill Magnetの確認画面へ渡す経路
- Codex DesktopまたはClaudeの既存利用プランへのhandoff
- install、実行、uninstall、transaction residueゼロのmacOS CI
- 実Mac一台以上でのFinderメニュー表示と確認画面起動の人手受入

## 対象外

- Mac App Store
- Developer ID署名
- Apple notarization
- `.app`、`.dmg`、`.pkg`による一般消費者向け配布
- Gatekeeperが識別済みdeveloperとして無警告で受理することの保証
- Skill MagnetによるCodex/Claudeの認証、課金、回答完了の保証

## リリース判定

macOSローカル導入版をGOにするには、次をすべて満たす。

1. GitHub ActionsのmacOS jobで、全テスト、standalone wheel gate、Finder Quick Action lifecycleがPASSする。
2. 固定release tagから導入できる。
3. 実MacでFinderの`Quick Actions`に`Skill Magnet`が表示される。
4. 実folderから起動した確認画面に、正しいproject pathとpackが表示される。
5. Cancelで外部AIを開かず終了できる。
6. uninstall後に`~/Library/Services/Skill Magnet.workflow`とtransaction residueが残らない。
7. 実機結果を[`macos-finder-friend-acceptance.md`](macos-finder-friend-acceptance.md)へ記録する。

CIはadapterの意味論とlifecycleを証明する。Finderメニューの目視表示はCIで代替せず、実機受入を別の証拠として扱う。

## 利用者への表示

この成果物は「macOSローカル導入版」または「macOS Finder Quick Action版」と表記する。「署名済みMacアプリ」「notarized app」「App Store版」とは表記しない。

