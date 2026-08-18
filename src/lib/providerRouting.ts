import type { ProviderChannel, ProviderConfig, ProviderQuality } from '../types'

export interface ProviderEstimate {
  activeChannels: ProviderChannel[]
  selectedChannel?: ProviderChannel
  autoChannel?: ProviderChannel
  defaultChannel?: ProviderChannel
  effectiveChannel?: ProviderChannel
  fixedUnavailable: boolean
}

function compareChannels(
  left: ProviderChannel,
  right: ProviderChannel,
  quality: ProviderQuality,
): number {
  const priceDifference = left.rates[quality] - right.rates[quality]
  if (priceDifference) return priceDifference
  const nameDifference = left.name.localeCompare(right.name)
  return nameDifference || left.id.localeCompare(right.id)
}

export function resolveProviderEstimate(
  config: ProviderConfig | undefined,
  choice: string,
  quality: ProviderQuality,
): ProviderEstimate {
  const activeChannels = config?.channels.filter((channel) => channel.active) ?? []
  const selectedChannel = activeChannels.find((channel) => channel.id === choice)
  const autoChannel = activeChannels
    .filter((channel) => (
      channel.currency === config?.routing.currency
      && channel.rates[quality] > 0
    ))
    .sort((left, right) => compareChannels(left, right, quality))[0]
  const defaultChannel = config?.routing.mode === 'fixed'
    ? activeChannels.find((channel) => channel.id === config.routing.fixedChannelId)
    : autoChannel
  const effectiveChannel = choice === 'auto'
    ? autoChannel
    : choice === 'default'
      ? defaultChannel
      : selectedChannel

  return {
    activeChannels,
    selectedChannel,
    autoChannel,
    defaultChannel,
    effectiveChannel,
    fixedUnavailable: choice !== 'auto' && choice !== 'default' && !selectedChannel,
  }
}
