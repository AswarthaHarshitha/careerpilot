import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { get } from './api'
import type { Task } from './types'

/** Polls a background task until it finishes, then invalidates the given query keys. */
export function useTask(taskId: number | null | undefined, invalidate: unknown[][] = [], onDone?: (t: Task) => void) {
  const qc = useQueryClient()
  const doneRef = useRef<number | null>(null)
  const q = useQuery({
    queryKey: ['task', taskId],
    queryFn: () => get<Task>(`/api/tasks/${taskId}`),
    enabled: !!taskId,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'succeeded' || s === 'failed' ? false : 900
    },
  })
  const task = q.data
  useEffect(() => {
    if (!task || doneRef.current === task.id) return
    if (task.status === 'succeeded' || task.status === 'failed') {
      doneRef.current = task.id
      invalidate.forEach((key) => qc.invalidateQueries({ queryKey: key }))
      onDone?.(task)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task?.status, task?.id])
  return task
}
