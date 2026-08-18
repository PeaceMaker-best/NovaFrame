import type { AssetItem, GenerationJob, WorkspaceSnapshot } from '../types'

export const demoAssets: AssetItem[] = [
  { id: 'cutout', name: '商品透明底图', url: '/demo/product-cutout.png', kind: 'product', dimensions: '528 × 764' },
  { id: 'accessory-pouch', name: '旅行收纳袋身份图', url: '/demo/accessory-pouch.png', kind: 'product', dimensions: '1024 × 1024' },
  { id: 'product-studio', name: '自然光商品摄影', url: '/demo/product-studio.png', kind: 'output', dimensions: '1024 × 1024' },
  { id: 'campaign', name: '夏日补水营销图', url: '/demo/campaign.png', kind: 'output', dimensions: '1024 × 1365' },
  { id: 'interior', name: '明亮家居场景', url: '/demo/interior.png', kind: 'scene', dimensions: '1536 × 1024' },
  { id: 'food', name: '餐饮氛围参考', url: '/demo/food-ad.png', kind: 'reference', dimensions: '1024 × 1024' },
  { id: 'fashion', name: '克制时装人像', url: '/demo/fashion.png', kind: 'reference', dimensions: '768 × 1024' },
]

export const demoWorkspace: WorkspaceSnapshot = {
  root: './workspace',
  products: [
    {
      id: 'MF-DEMO-001',
      name: 'MF-DEMO-001 · 夏日补水喷雾',
      assetCount: 2,
      taskCount: 2,
      promptCount: 10,
      outputCount: 6,
      readiness: 'ready',
      thumbnail: '/demo/product-studio.png',
      updatedAt: '刚刚',
    },
    {
      id: 'MF-DEMO-002',
      name: 'MF-DEMO-002 · 家居香氛套装',
      assetCount: 8,
      taskCount: 4,
      promptCount: 20,
      outputCount: 13,
      readiness: 'stale',
      thumbnail: '/demo/interior.png',
      updatedAt: '12 分钟前',
    },
    {
      id: 'MF-DEMO-003',
      name: 'MF-DEMO-003 · 轻食餐具',
      assetCount: 5,
      taskCount: 3,
      promptCount: 15,
      outputCount: 8,
      readiness: 'blocked',
      thumbnail: '/demo/food-ad.png',
      updatedAt: '昨天',
    },
  ],
  accessories: [{ id: '旅行收纳袋', assetCount: 1 }],
  stats: { products: 3, accessories: 7, tasks: 9, prompts: 45, outputs: 27, pendingReview: 8 },
  liveGenerationEnabled: false,
}

export const initialJobs: GenerationJob[] = [
  { id: 'job-1', title: '自然晨光场景', product: 'MF-DEMO-001', shot: 'lifestyle-scene', status: 'running', progress: 68, createdAt: '10:32', thumbnail: '/demo/product-studio.png' },
  { id: 'job-2', title: '主图 · 纯净棚拍', product: 'MF-DEMO-001', shot: 'main', status: 'queued', progress: 0, createdAt: '10:33' },
  { id: 'job-3', title: '材质细节说明', product: 'MF-DEMO-002', shot: 'detail', status: 'succeeded', progress: 100, createdAt: '10:28', thumbnail: '/demo/interior.png' },
]
