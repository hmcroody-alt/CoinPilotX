import { Share } from "react-native";
import { pulseApi } from "../apiClient";
import { PulseComment, PulsePost, FeedResponse } from "./types";

export function loadFeed(offset = 0, limit = 12, profile = "") {
  const params = new URLSearchParams({ tab: "for_you", offset: String(offset), limit: String(limit) });
  if (profile) params.set("profile", profile);
  return pulseApi<FeedResponse>(`/api/pulse/feed?${params.toString()}`);
}

export async function loadPost(postId: number) {
  return pulseApi<{ ok: boolean; post: PulsePost; comments: PulseComment[] }>(`/api/pulse/posts/${postId}`);
}

export async function createPost(payload: { body: string; mediaIds?: number[]; postType?: string; title?: string; tags?: string[] }) {
  return pulseApi<{ ok: boolean; post?: PulsePost; post_id?: number; message?: string }>("/api/pulse/posts", {
    method: "POST",
    body: JSON.stringify({
      body: payload.body,
      title: payload.title || "",
      post_type: payload.postType || (payload.mediaIds?.length ? "image" : "text"),
      media_ids: payload.mediaIds || [],
      tags: payload.tags || extractTags(payload.body),
      visibility: "public"
    })
  });
}

export async function editPost(postId: number, body: string) {
  return pulseApi<{ ok: boolean; post_id: number; message: string }>(`/api/pulse/posts/${postId}`, {
    method: "PATCH",
    body: JSON.stringify({ body, title: "", visibility: "public" })
  });
}

export async function deletePost(postId: number) {
  return pulseApi<{ ok: boolean; message: string; post_id: number }>(`/api/pulse/posts/${postId}`, { method: "DELETE" });
}

export async function reactToPost(postId: number, reactionType = "fire") {
  return pulseApi<{ ok: boolean; post_id: number; reaction_type: string; reaction_counts: Record<string, number>; reactions_count: number; removed?: boolean }>(`/api/pulse/posts/${postId}/react`, {
    method: "POST",
    body: JSON.stringify({ reaction_type: reactionType })
  });
}

export async function repostPost(postId: number, note = "") {
  return pulseApi<{ ok: boolean; post_id: number; message: string }>(`/api/pulse/posts/${postId}/repost`, {
    method: "POST",
    body: JSON.stringify({ note })
  });
}

export async function loadComments(postId: number) {
  return pulseApi<{ ok: boolean; comments: PulseComment[] }>(`/api/pulse/posts/${postId}/comments`);
}

export async function addComment(postId: number, body: string, parentCommentId?: number) {
  return pulseApi<{ ok: boolean; comment?: PulseComment; comments_count?: number; message?: string }>(`/api/pulse/posts/${postId}/comments`, {
    method: "POST",
    body: JSON.stringify({ body, parent_comment_id: parentCommentId || null })
  });
}

export async function sharePost(post: PulsePost) {
  const url = post.permalink?.startsWith("http") ? post.permalink : `https://pulsesoc.com${post.permalink || `/pulse/post/${post.id}`}`;
  await Share.share({ title: post.title || "PulseSoc post", message: `${post.body || "PulseSoc post"}\n${url}`, url });
  return { ok: true };
}

function extractTags(body: string) {
  return Array.from(new Set((body.match(/#[A-Za-z0-9_]+/g) || []).map(tag => tag.slice(1).toLowerCase()))).slice(0, 12);
}
