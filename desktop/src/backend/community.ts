import { invoke } from '@tauri-apps/api/core'

export type PostKind = 'discussion' | 'peer_review' | 'resource'
export type CommunityReply = { id: number; postId: number; body: string; author: string; accepted: boolean; createdAt: string }
export type CommunityPost = {
  id: number
  title: string
  body: string
  kind: PostKind
  projectId: number | null
  projectTitle: string | null
  author: string
  likes: number
  liked: boolean
  bookmarked: boolean
  replies: CommunityReply[]
  createdAt: string
  updatedAt: string
}
export type CommunityGroup = { id: number; name: string; topic: string; schedule: string; joined: boolean; memberCount: number; createdAt: string }
export type CommunityRoom = {
  id: number
  groupId: number | null
  title: string
  topic: string
  startAt: string
  durationMinutes: number
  status: 'scheduled' | 'cancelled'
  reserved: boolean
  reservationCount: number
  reminderAt: string | null
  createdAt: string
}
export type CommunityStats = { posts: number; replies: number; reviews: number; acceptedReplies: number; likes: number; bookmarks: number; joinedGroups: number; activeReservations: number }
export type CommunityOverview = { posts: CommunityPost[]; groups: CommunityGroup[]; rooms: CommunityRoom[]; stats: CommunityStats; now: string }
export type PostInput = { title: string; body: string; kind: PostKind; projectId: number | null }
export type GroupInput = { name: string; topic: string; schedule: string }
export type RoomInput = { groupId: number | null; title: string; topic: string; startAt: string; durationMinutes: number }
export type CommunityReminder = { id: number; roomId: number; title: string; startAt: string; remindAt: string; deliveredAt: string | null }

export const communityBackend = {
  overview: (kind?: PostKind, query?: string) => invoke<CommunityOverview>('get_community_overview', { kind: kind ?? null, query: query ?? null }),
  createPost: (input: PostInput) => invoke<CommunityPost>('create_community_post', { input }),
  reply: (postId: number, body: string) => invoke<CommunityReply>('reply_community_post', { postId, body }),
  acceptReply: (replyId: number, accepted: boolean) => invoke<CommunityReply>('accept_community_reply', { replyId, accepted }),
  reaction: (postId: number, kind: 'like' | 'bookmark') => invoke<CommunityPost>('toggle_community_reaction', { postId, kind }),
  createGroup: (input: GroupInput) => invoke<CommunityGroup>('create_community_group', { input }),
  joinGroup: (id: number, joined: boolean) => invoke<CommunityGroup>('toggle_community_group', { id, joined }),
  createRoom: (input: RoomInput) => invoke<CommunityRoom>('create_community_room', { input }),
  reserve: (id: number) => invoke<CommunityRoom>('reserve_community_room', { id }),
  cancelReservation: (id: number) => invoke<CommunityRoom>('cancel_community_reservation', { id }),
  cancelRoom: (id: number) => invoke<CommunityRoom>('cancel_community_room', { id }),
  claimDueReminders: () => invoke<CommunityReminder[]>('claim_community_reminders'),
}
