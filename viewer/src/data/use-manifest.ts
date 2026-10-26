import { useQuery } from '@tanstack/react-query'
import { loadManifest } from './manifest'

/** The manifest is read once per page load; no refetching on focus or retries. */
export function useManifest() {
  return useQuery({
    queryKey: ['manifest'],
    queryFn: loadManifest,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
  })
}
