// 首次设置向导状态 hook（iter 89 零配置开箱即用）。
// react-query 缓存共享：App 守卫 / Layout 横幅 / 设置页 / 登录页共用同一份 ['setup'] 查询。
import { useQuery } from '@tanstack/react-query'
import { getSetupStatus, type SetupStatus } from './api'

export const SETUP_QUERY_KEY = ['setup'] as const

export function useSetupStatus() {
  return useQuery<SetupStatus>({
    queryKey: SETUP_QUERY_KEY,
    queryFn: getSetupStatus,
    retry: 1,
    staleTime: 30_000,
  })
}
