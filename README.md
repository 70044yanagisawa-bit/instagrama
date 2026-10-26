# インスタ自動投稿（instagrama）

iPhone のカメラロールから筋トレの写真・動画を AI が選び、決まった型で編集して、毎晩 20:00 に Instagram へ投稿する仕組み。

```
iPhone（ショートカット 毎日17:00）
  └─ その日の写真・動画を Google ドライブの「insta-inbox」へ
GitHub Actions（毎日17:30）
  ├─ 新しい素材を AI（OpenAI）が見て、筋トレ関係か・映えるかを採点（動画はコマを切り出す）
  ├─ 一番良い1枚を prompts/edit.md の型で編集 → 4:5 に整える
  └─ 確認期間中は LINE に「この内容で投稿していい？ OK / NG」を送る
GitHub Actions（毎日20:00）
  └─ OK なら Instagram に投稿（返事なし・NG なら投稿しない）。確認期間が終わったら確認なしで投稿
```

- 今日の素材がなければ、過去の未投稿の候補（ストック）から選ぶ
- 投稿した素材・NG にした素材は二度と使わない。返事がなかった素材は候補に残る
- 記録は `state/state.json`（Actions が自動で更新・保存する）

## ふだん触るところ

| やりたいこと | 場所 |
| --- | --- |
| 編集の型を変える | `prompts/edit.md` |
| 選ぶ基準を変える | `prompts/select.md` |
| キャプション・投稿時刻・確認期間 | `config.yaml` |
| 今すぐ試す | GitHub の Actions → 「Instagram 自動投稿」→ Run workflow（prepare で準備＋LINE 確認、publish で投稿） |

確認期間は `config.yaml` の `approval.until`（今は 2026-11-23 まで）。ずっと確認するなら `"always"`、すぐ完全自動にするなら `"never"`。

編集の型を手元で試すとき（OpenAI のキーだけで動く）：

```
pip install -r requirements.txt
OPENAI_API_KEY=sk-... python -m autopost try ~/Desktop/sample.jpg   # out/ に保存される
```

## 最初の設定（1回だけ）

最後に GitHub に登録する値を ★ で示しています。

### 1. OpenAI

1. https://platform.openai.com で API キーを作る（★ `OPENAI_API_KEY`）
2. Billing でクレジットを入れる。1日1枚の編集＋素材の採点なので、月に数百円〜千円程度が目安（モデルと枚数による）

### 2. Google ドライブ（素材の受け取り口）

1. Google ドライブに `insta-inbox` フォルダを作る。URL の `folders/` の後ろの文字列が ★ `DRIVE_FOLDER_ID`
2. https://console.cloud.google.com でプロジェクトを作り、「Google Drive API」を有効にする
3. 「IAM と管理」→「サービスアカウント」で作成 →「鍵」→「JSON」でダウンロード。ファイルの中身まるごとが ★ `GOOGLE_SERVICE_ACCOUNT_JSON`
4. `insta-inbox` フォルダを、サービスアカウントのメールアドレス（`…@….iam.gserviceaccount.com`）に「閲覧者」で共有する

### 3. iPhone のショートカット（毎日自動で送る）

前提：iPhone に Google ドライブのアプリを入れてログインし、「ファイル」アプリの「場所」で Google ドライブをオンにしておく。

1. ショートカット アプリ →「オートメーション」→「＋」→「時刻」→ 17:00・毎日・「すぐに実行」
2. 「新規の空のショートカット」で次の2つを並べる
   - 「写真を検索」：フィルタ「撮影日」が「過去 1 日以内」
   - 「ファイルを保存」：保存先を Google ドライブの `insta-inbox`、「保存先を尋ねる」はオフ
3. 一度手動で実行し、ドライブにファイルが入ることを確認する

動画が多い日は通信量が増えるので、Wi-Fi のときだけにしたい場合は「Wi-Fi」のオートメーションにする手もあります。カメラロールの写真は筋トレ以外も AI（OpenAI）に縮小画像で送られて判定されます。

### 4. LINE（確認・お知らせ用）

1. https://developers.line.biz でプロバイダーを作り、Messaging API のチャネルを作る（LINE 公式アカウントが一緒にできる。無料プランで足りる）
2. 「チャネル基本設定」のチャネルシークレット → ★ `LINE_CHANNEL_SECRET`（Worker 用）
3. 「Messaging API 設定」でチャネルアクセストークン（長期）を発行 → ★ `LINE_CHANNEL_ACCESS_TOKEN`
4. 同じ画面の QR コードで自分のスマホから友だち追加。LINE Official Account Manager の応答設定で「応答メッセージ」はオフ、「Webhook」はオン
5. Webhook URL は手順 5 のあとで設定する

### 5. Cloudflare Worker（LINE の OK/NG と画像の置き場）

保存場所（R2 の `insta-autopost-media`、KV の `insta-autopost`）は作成済み。

1. Cloudflare の Workers & Pages → Create → Import a repository →このリポジトリを選ぶ。Root directory は `worker`
2. デプロイされた URL（例：`https://insta-autopost.mgr-lab.workers.dev`）→ ★ `WORKER_URL`
3. Settings → Variables and Secrets に「Secret」で追加：
   - `WORKER_TOKEN`：自分で決めた長いランダムな文字列（パスワード生成ツールで 40 文字くらい）→ ★ 同じ値を GitHub にも
   - `LINE_CHANNEL_SECRET`、`LINE_CHANNEL_ACCESS_TOKEN`：手順 4 の値
4. LINE Developers の Webhook URL に `WORKER_URL` + `/line/webhook` を入れて「検証」
5. LINE で公式アカウントに `id` と送ると、自分のユーザー ID（U から始まる）が返ってくる → ★ `LINE_USER_ID`。Worker の Secret にも `LINE_USER_ID` で追加（自分以外の OK/NG を無視するため）

### 6. Instagram

Facebook ページの連携は不要（Instagram ログイン版の API を使う）。アカウントはプロアカウント（ビジネス／クリエイター）であること。

1. https://developers.facebook.com でアプリを作成 → ユースケース「Instagram でメッセージとコンテンツを管理」
2. 「Instagram ログインによる API 設定」→「アクセストークンを生成」でアカウントを追加
   - 追加できないときは、アプリの「役割」で自分の Instagram アカウントをテスターに追加し、Instagram アプリ側（設定 → ウェブサイトの許可）で承認する
3. 表示されたトークン → ★ `IG_ACCESS_TOKEN`、Instagram のユーザー ID → ★ `IG_USER_ID`
4. 権限に `instagram_business_basic` と `instagram_business_content_publish` が入っていることを確認

トークンは 60 日で切れるが、投稿のたびに 1 週間ごとに自動で延長している。もし切れたら、手順 2 でトークンを作り直して `IG_ACCESS_TOKEN` を入れ替えるだけでよい。

### 7. GitHub に登録

このリポジトリの Settings → Secrets and variables → Actions → New repository secret で、★ の値を登録：

`OPENAI_API_KEY` / `GOOGLE_SERVICE_ACCOUNT_JSON` / `DRIVE_FOLDER_ID` / `WORKER_URL` / `WORKER_TOKEN` / `LINE_CHANNEL_ACCESS_TOKEN` / `LINE_USER_ID` / `IG_ACCESS_TOKEN` / `IG_USER_ID`

### 8. 動作確認

1. Actions →「Instagram 自動投稿」→ Run workflow → `prepare` を実行 → LINE に画像と OK/NG が届く
2. OK を押して `publish` を実行 → 20:00 前なら 20:00 まで待ってから投稿される（40 分以上前だとエラーで止まる）

## 注意

- GitHub Actions の定期実行は混雑で数分〜数十分遅れることがある（そのため 19:40 に起動して 20:00 まで待つ）
- 写真・動画の判定と編集の記録は `state/state.json` に残る。素材を使い直したいときは、その素材の `status` を `candidate` に戻す
- テスト：`python -m pytest`
