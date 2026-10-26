// LINE の OK/NG ボタンを受け取り、投稿画像を公開 URL で配る小さな Worker。
//   GET  /media/<key>        画像（Instagram と LINE が取りに来る。誰でも見られる）
//   PUT  /media/<key>        画像を置く（要トークン）
//   POST /pending/<id>       今日の確認待ちを登録（要トークン）
//   GET  /approval/<id>      OK/NG の結果（要トークン）。DELETE で締め切り
//   GET/PUT /kv/<key>        Instagram のトークンなどを保管（要トークン）
//   POST /line/webhook       LINE からの通知

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const [, route, ...rest] = url.pathname.split("/");
    const key = decodeURIComponent(rest.join("/"));

    if (route === "media" && request.method === "GET") {
      const obj = await env.MEDIA.get(key);
      if (!obj) return new Response("not found", { status: 404 });
      return new Response(obj.body, {
        headers: { "Content-Type": "image/jpeg", "Cache-Control": "public, max-age=86400" },
      });
    }

    if (route === "line" && key === "webhook" && request.method === "POST") {
      return lineWebhook(request, env);
    }

    if (request.headers.get("Authorization") !== `Bearer ${env.WORKER_TOKEN}`) {
      return new Response("unauthorized", { status: 401 });
    }

    if (route === "media" && request.method === "PUT") {
      await env.MEDIA.put(key, request.body, { httpMetadata: { contentType: "image/jpeg" } });
      return json({ ok: true });
    }
    if (route === "pending" && request.method === "POST") {
      await env.KV.put(`approval:${key}`, "pending", { expirationTtl: 60 * 60 * 24 * 7 });
      await env.KV.put("latest", key);
      return json({ ok: true });
    }
    if (route === "approval" && request.method === "GET") {
      return json({ status: (await env.KV.get(`approval:${key}`)) || "none" });
    }
    if (route === "approval" && request.method === "DELETE") {
      // 投稿時刻を過ぎたら締め切る（あとから OK を押しても投稿されないように）
      await env.KV.delete(`approval:${key}`);
      return json({ ok: true });
    }
    if (route === "kv" && request.method === "GET") {
      const value = await env.KV.get(`value:${key}`);
      return value === null ? new Response("not found", { status: 404 }) : new Response(value);
    }
    if (route === "kv" && request.method === "PUT") {
      await env.KV.put(`value:${key}`, await request.text());
      return json({ ok: true });
    }
    return new Response("not found", { status: 404 });
  },
};

async function lineWebhook(request, env) {
  const body = await request.text();
  if (!(await validSignature(body, request.headers.get("x-line-signature"), env.LINE_CHANNEL_SECRET))) {
    return new Response("bad signature", { status: 401 });
  }
  const { events = [] } = JSON.parse(body);
  for (const ev of events) {
    const userId = ev.source?.userId;
    const text = ev.type === "message" && ev.message.type === "text" ? ev.message.text.trim() : "";

    // 初期設定用：「id」と送ると自分の LINE ユーザー ID を返す
    if (text.toLowerCase() === "id") {
      await reply(env, ev.replyToken, `あなたの LINE ユーザーID：\n${userId}`);
      continue;
    }
    if (env.LINE_USER_ID && userId !== env.LINE_USER_ID) continue;

    let action, postId;
    if (ev.type === "postback") {
      [action, postId] = ev.postback.data.split(":");
    } else if (/^(ok|おk|オッケー|いいよ)$/i.test(text)) {
      [action, postId] = ["approve", await env.KV.get("latest")];
    } else if (/^(ng|だめ|ダメ)$/i.test(text)) {
      [action, postId] = ["reject", await env.KV.get("latest")];
    } else {
      continue;
    }
    if (!postId) continue;

    const current = await env.KV.get(`approval:${postId}`);
    if (!current) {
      await reply(env, ev.replyToken, "この確認はもう締め切られています。");
      continue;
    }
    const status = action === "approve" ? "approved" : "rejected";
    await env.KV.put(`approval:${postId}`, status, { expirationTtl: 60 * 60 * 24 * 7 });
    await reply(
      env,
      ev.replyToken,
      status === "approved" ? "OK！時間になったら投稿します 👍" : "了解、今日は投稿しません。この写真は今後も使いません。",
    );
  }
  return json({ ok: true });
}

async function validSignature(body, signature, secret) {
  if (!signature || !secret) return false;
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  return expected === signature;
}

async function reply(env, replyToken, text) {
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` },
    body: JSON.stringify({ replyToken, messages: [{ type: "text", text }] }),
  });
}

function json(data) {
  return new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
}
