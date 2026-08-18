import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import Konva from 'konva'
import { Image as KonvaImage, Layer, Line, Rect, Stage, Text, Transformer } from 'react-konva'
import useImage from 'use-image'
import type { CanvasImageNode, CanvasNode, CanvasTextNode } from '../types'

export const ARTBOARD = { x: 300, y: 140, width: 640, height: 640 }

export type CanvasViewport = {
  x: number
  y: number
  zoom: number
  mode?: 'fit' | 'custom'
}

export type StudioCanvasHandle = {
  exportPng: (targetSize?: number) => string | undefined
  fitArtboard: () => void
  zoomTo: (zoom: number) => void
}

type Props = {
  nodes: CanvasNode[]
  onNodesChange: (nodes: CanvasNode[]) => void
  selectedIds: string[]
  onSelectionChange: (ids: string[]) => void
  tool: 'select' | 'hand'
  viewport: CanvasViewport
  onViewportChange: (viewport: CanvasViewport) => void
  artboardLabel?: string
  readOnly?: boolean
}

type ShapeProps<T extends CanvasNode> = {
  node: T
  canDrag: boolean
  onSelect: (event: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => void
  onDragStart: (event: Konva.KonvaEventObject<DragEvent>) => void
  onDragMove: (event: Konva.KonvaEventObject<DragEvent>) => void
  onDragEnd: (event: Konva.KonvaEventObject<DragEvent>) => void
  onRef: (id: string, shape: Konva.Node | null) => void
  onImageState?: (id: string, state: 'loading' | 'ready' | 'failed' | 'removed') => void
}

function ImageNode({ node, canDrag, onSelect, onDragStart, onDragMove, onDragEnd, onRef, onImageState }: ShapeProps<CanvasImageNode>) {
  const [image, status] = useImage(node.src, 'anonymous')
  const shapeRef = useRef<Konva.Image>(null)

  useEffect(() => {
    onRef(node.id, shapeRef.current)
    return () => onRef(node.id, null)
  }, [node.id, onRef])

  useEffect(() => {
    onImageState?.(node.id, status === 'loaded' ? 'ready' : status === 'failed' ? 'failed' : 'loading')
    return () => onImageState?.(node.id, 'removed')
  }, [node.id, onImageState, status])

  return (
    <KonvaImage
      ref={shapeRef}
      image={image}
      x={node.x}
      y={node.y}
      width={node.width}
      height={node.height}
      rotation={node.rotation ?? 0}
      opacity={node.opacity ?? 1}
      listening={canDrag}
      draggable={canDrag}
      onMouseDown={onSelect}
      onTap={onSelect}
      onDragStart={onDragStart}
      onDragMove={onDragMove}
      onDragEnd={onDragEnd}
    />
  )
}

function TextNode({ node, canDrag, onSelect, onDragStart, onDragMove, onDragEnd, onRef }: ShapeProps<CanvasTextNode>) {
  const shapeRef = useRef<Konva.Text>(null)

  useEffect(() => {
    onRef(node.id, shapeRef.current)
    return () => onRef(node.id, null)
  }, [node.id, onRef])

  return (
    <Text
      ref={shapeRef}
      x={node.x}
      y={node.y}
      width={node.width}
      text={node.text}
      fontSize={node.fontSize}
      fontFamily={node.fontFamily ?? 'Inter, Arial, sans-serif'}
      fontStyle={node.fontStyle ?? 'normal'}
      fill={node.fill}
      opacity={node.opacity ?? 1}
      rotation={node.rotation ?? 0}
      align={node.align ?? 'left'}
      lineHeight={1.1}
      listening={canDrag}
      draggable={canDrag}
      onMouseDown={onSelect}
      onTap={onSelect}
      onDragStart={onDragStart}
      onDragMove={onDragMove}
      onDragEnd={onDragEnd}
    />
  )
}

type Marquee = { x: number; y: number; width: number; height: number }
type DragSession = {
  anchorId: string
  anchorX: number
  anchorY: number
  positions: Map<string, { x: number; y: number }>
}

function normalizedRect(start: { x: number; y: number }, end: { x: number; y: number }): Marquee {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  }
}

export const StudioCanvas = forwardRef<StudioCanvasHandle, Props>(function StudioCanvas({
  nodes,
  onNodesChange,
  selectedIds,
  onSelectionChange,
  tool,
  viewport,
  onViewportChange,
  artboardLabel = '画板',
  readOnly = false,
}, forwardedRef) {
  const containerRef = useRef<HTMLDivElement>(null)
  const stageRef = useRef<Konva.Stage>(null)
  const backdropLayerRef = useRef<Konva.Layer>(null)
  const overlayLayerRef = useRef<Konva.Layer>(null)
  const transformerRef = useRef<Konva.Transformer>(null)
  const nodeRefs = useRef(new Map<string, Konva.Node>())
  const marqueeStart = useRef<{ x: number; y: number } | undefined>(undefined)
  const dragSession = useRef<DragSession | undefined>(undefined)
  const [marquee, setMarquee] = useState<Marquee>()
  const [snapGuides, setSnapGuides] = useState<{ vertical?: number; horizontal?: number }>({})
  const [size, setSize] = useState({ width: 900, height: 720 })
  const [imageStates, setImageStates] = useState<Map<string, 'loading' | 'ready' | 'failed'>>(new Map())

  const rememberNodeRef = useMemo(() => (id: string, shape: Konva.Node | null) => {
    if (shape) nodeRefs.current.set(id, shape)
    else nodeRefs.current.delete(id)
  }, [])

  const rememberImageState = useMemo(() => (id: string, state: 'loading' | 'ready' | 'failed' | 'removed') => {
    setImageStates((current) => {
      const next = new Map(current)
      state === 'removed' ? next.delete(id) : next.set(id, state)
      return next
    })
  }, [])

  const fitArtboard = () => {
    if (readOnly) return
    const horizontalPadding = 92
    const verticalPadding = 116
    const nextZoom = Math.min(1.35, Math.max(0.35, Math.min(
      (size.width - horizontalPadding) / ARTBOARD.width,
      (size.height - verticalPadding) / ARTBOARD.height,
    )))
    onViewportChange({
      zoom: Number(nextZoom.toFixed(3)),
      x: (size.width - ARTBOARD.width * nextZoom) / 2 - ARTBOARD.x * nextZoom,
      y: (size.height - ARTBOARD.height * nextZoom) / 2 - ARTBOARD.y * nextZoom,
      mode: 'fit',
    })
  }

  const zoomTo = (requestedZoom: number) => {
    if (readOnly) return
    const nextZoom = Math.min(1.5, Math.max(0.35, requestedZoom))
    const anchor = { x: size.width / 2, y: size.height / 2 }
    const worldPoint = {
      x: (anchor.x - viewport.x) / viewport.zoom,
      y: (anchor.y - viewport.y) / viewport.zoom,
    }
    onViewportChange({
      zoom: Number(nextZoom.toFixed(3)),
      x: anchor.x - worldPoint.x * nextZoom,
      y: anchor.y - worldPoint.y * nextZoom,
      mode: 'custom',
    })
  }

  useImperativeHandle(forwardedRef, () => ({
    fitArtboard,
    zoomTo,
    exportPng: (targetSize = 1024) => {
      const stage = stageRef.current
      if (!stage) return undefined
      const imageNodeIds = nodes.filter((node) => node.type === 'image' && node.visible !== false).map((node) => node.id)
      if (imageNodeIds.some((id) => imageStates.get(id) === 'failed')) throw new Error('画板中有图片加载失败，请检查素材后重试')
      if (imageNodeIds.some((id) => imageStates.get(id) !== 'ready')) throw new Error('画板图片仍在加载，请稍后再导出')
      const previousPosition = stage.position()
      const previousScale = stage.scale()
      const backdropVisible = backdropLayerRef.current?.visible() ?? true
      const overlayVisible = overlayLayerRef.current?.visible() ?? true
      try {
        backdropLayerRef.current?.visible(false)
        overlayLayerRef.current?.visible(false)
        stage.position({ x: 0, y: 0 })
        stage.scale({ x: 1, y: 1 })
        stage.draw()
        return stage.toDataURL({
          x: ARTBOARD.x,
          y: ARTBOARD.y,
          width: ARTBOARD.width,
          height: ARTBOARD.height,
          pixelRatio: targetSize / ARTBOARD.width,
          mimeType: 'image/png',
        })
      } finally {
        stage.position(previousPosition)
        stage.scale(previousScale)
        backdropLayerRef.current?.visible(backdropVisible)
        overlayLayerRef.current?.visible(overlayVisible)
        stage.draw()
      }
    },
  }))

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const update = () => setSize({ width: element.clientWidth, height: element.clientHeight })
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if ((viewport.mode ?? 'fit') === 'fit' && size.width > 0 && size.height > 0) fitArtboard()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.width, size.height])

  useEffect(() => {
    const transformer = transformerRef.current
    if (!transformer) return
    const selected = readOnly ? [] : selectedIds
      .map((id) => nodes.find((node) => node.id === id))
      .filter((node): node is CanvasNode => Boolean(node && node.visible !== false && node.locked !== true && node.id !== 'scene-background'))
      .map((node) => nodeRefs.current.get(node.id))
      .filter((node): node is Konva.Node => Boolean(node))
    transformer.nodes(selected)
    transformer.getLayer()?.batchDraw()
  }, [nodes, readOnly, selectedIds])

  const worldPointer = () => {
    const stage = stageRef.current
    const pointer = stage?.getPointerPosition()
    if (!stage || !pointer) return undefined
    return stage.getAbsoluteTransform().copy().invert().point(pointer)
  }

  const selectNode = (id: string, event: Konva.KonvaEventObject<MouseEvent | TouchEvent>) => {
    if (tool !== 'select') return
    const nativeEvent = event.evt as MouseEvent
    const additive = Boolean(nativeEvent.shiftKey || nativeEvent.metaKey || nativeEvent.ctrlKey)
    if (!additive) onSelectionChange([id])
    else if (selectedIds.includes(id)) onSelectionChange(selectedIds.filter((item) => item !== id))
    else onSelectionChange([...selectedIds, id])
  }

  const handleDragStart = (id: string, event: Konva.KonvaEventObject<DragEvent>) => {
    const activeIds = selectedIds.includes(id) ? selectedIds : [id]
    const positions = new Map<string, { x: number; y: number }>()
    activeIds.forEach((activeId) => {
      const node = nodes.find((item) => item.id === activeId)
      if (node && node.locked !== true && node.id !== 'scene-background') positions.set(activeId, { x: node.x, y: node.y })
    })
    dragSession.current = { anchorId: id, anchorX: event.target.x(), anchorY: event.target.y(), positions }
  }

  const handleDragMove = (id: string, event: Konva.KonvaEventObject<DragEvent>) => {
    const session = dragSession.current
    if (!session || session.anchorId !== id) return
    if (session.positions.size > 1) {
      const dx = event.target.x() - session.anchorX
      const dy = event.target.y() - session.anchorY
      session.positions.forEach((position, selectedId) => {
        if (selectedId === id) return
        nodeRefs.current.get(selectedId)?.position({ x: position.x + dx, y: position.y + dy })
      })
      event.target.getLayer()?.batchDraw()
      return
    }

    const stage = stageRef.current
    if (!stage) return
    const box = event.target.getClientRect({ relativeTo: stage, skipShadow: true })
    const xTargets = [ARTBOARD.x, ARTBOARD.x + ARTBOARD.width / 2, ARTBOARD.x + ARTBOARD.width]
    const yTargets = [ARTBOARD.y, ARTBOARD.y + ARTBOARD.height / 2, ARTBOARD.y + ARTBOARD.height]
    const xEdges = [box.x, box.x + box.width / 2, box.x + box.width]
    const yEdges = [box.y, box.y + box.height / 2, box.y + box.height]
    const threshold = 6 / viewport.zoom
    let bestX: { delta: number; guide: number } | undefined
    let bestY: { delta: number; guide: number } | undefined
    xTargets.forEach((target) => xEdges.forEach((edge) => {
      const delta = target - edge
      if (Math.abs(delta) <= threshold && (!bestX || Math.abs(delta) < Math.abs(bestX.delta))) bestX = { delta, guide: target }
    }))
    yTargets.forEach((target) => yEdges.forEach((edge) => {
      const delta = target - edge
      if (Math.abs(delta) <= threshold && (!bestY || Math.abs(delta) < Math.abs(bestY.delta))) bestY = { delta, guide: target }
    }))
    if (bestX) event.target.x(event.target.x() + bestX.delta)
    if (bestY) event.target.y(event.target.y() + bestY.delta)
    setSnapGuides({ vertical: bestX?.guide, horizontal: bestY?.guide })
  }

  const handleDragEnd = (id: string, event: Konva.KonvaEventObject<DragEvent>) => {
    const session = dragSession.current
    dragSession.current = undefined
    setSnapGuides({})
    if (readOnly || !session || session.anchorId !== id) return
    const dx = event.target.x() - session.anchorX
    const dy = event.target.y() - session.anchorY
    onNodesChange(nodes.map((node) => {
      const position = session.positions.get(node.id)
      return position ? { ...node, x: position.x + dx, y: position.y + dy } : node
    }))
  }

  const transformSelection = () => {
    if (readOnly) return
    const selected = new Set(selectedIds)
    const next = nodes.map((node) => {
      if (!selected.has(node.id) || node.locked || node.id === 'scene-background') return node
      const shape = nodeRefs.current.get(node.id)
      if (!shape) return node
      const scaleX = shape.scaleX()
      const scaleY = shape.scaleY()
      shape.scale({ x: 1, y: 1 })
      if (node.type === 'image') {
        return { ...node, x: shape.x(), y: shape.y(), rotation: shape.rotation(), width: Math.max(24, node.width * scaleX), height: Math.max(24, node.height * scaleY) }
      }
      return { ...node, x: shape.x(), y: shape.y(), rotation: shape.rotation(), width: Math.max(80, node.width * scaleX), fontSize: Math.max(8, node.fontSize * scaleY) }
    })
    onNodesChange(next)
  }

  const guideLines = useMemo(() => [
    [ARTBOARD.x + 32, ARTBOARD.y + 32, ARTBOARD.x + ARTBOARD.width - 32, ARTBOARD.y + 32],
    [ARTBOARD.x + ARTBOARD.width - 32, ARTBOARD.y + 32, ARTBOARD.x + ARTBOARD.width - 32, ARTBOARD.y + ARTBOARD.height - 32],
    [ARTBOARD.x + ARTBOARD.width - 32, ARTBOARD.y + ARTBOARD.height - 32, ARTBOARD.x + 32, ARTBOARD.y + ARTBOARD.height - 32],
    [ARTBOARD.x + 32, ARTBOARD.y + ARTBOARD.height - 32, ARTBOARD.x + 32, ARTBOARD.y + 32],
  ], [])

  const selectedNodes = selectedIds.map((id) => nodes.find((node) => node.id === id)).filter(Boolean) as CanvasNode[]
  const transformableNodes = selectedNodes.filter((node) => node.locked !== true && node.id !== 'scene-background')
  const singleSelected = transformableNodes.length === 1 ? transformableNodes[0] : undefined

  return (
    <div className={`studio-canvas ${tool === 'hand' ? 'is-hand' : ''} ${readOnly ? 'is-read-only' : ''}`} ref={containerRef}>
      <div className="canvas-dot-grid" />
      <Stage
        ref={stageRef}
        width={size.width}
        height={size.height}
        scaleX={viewport.zoom}
        scaleY={viewport.zoom}
        x={viewport.x}
        y={viewport.y}
        draggable={!readOnly && tool === 'hand'}
        onDragEnd={(event) => {
          if (!readOnly && tool === 'hand') onViewportChange({ ...viewport, x: event.target.x(), y: event.target.y(), mode: 'custom' })
        }}
        onMouseDown={(event) => {
          if (tool !== 'select' || event.target !== event.target.getStage()) return
          const point = worldPointer()
          if (!point) return
          marqueeStart.current = point
          setMarquee({ x: point.x, y: point.y, width: 0, height: 0 })
        }}
        onMouseMove={() => {
          const start = marqueeStart.current
          const point = worldPointer()
          if (start && point) setMarquee(normalizedRect(start, point))
        }}
        onMouseUp={(event) => {
          const start = marqueeStart.current
          const point = worldPointer()
          marqueeStart.current = undefined
          setMarquee(undefined)
          if (!start || !point) return
          const box = normalizedRect(start, point)
          const additive = event.evt.shiftKey || event.evt.metaKey || event.evt.ctrlKey
          if (box.width < 3 / viewport.zoom && box.height < 3 / viewport.zoom) {
            if (!additive) onSelectionChange([])
            return
          }
          const hits = nodes.filter((node) => {
            if (node.visible === false || node.locked === true || node.id === 'scene-background') return false
            const shape = nodeRefs.current.get(node.id)
            return shape ? Konva.Util.haveIntersection(box, shape.getClientRect({ relativeTo: stageRef.current ?? undefined, skipShadow: true })) : false
          }).map((node) => node.id)
          onSelectionChange(additive ? Array.from(new Set([...selectedIds, ...hits])) : hits)
        }}
        onWheel={(event) => {
          event.evt.preventDefault()
          if (readOnly) return
          const stage = event.target.getStage()
          const pointer = stage?.getPointerPosition()
          if (!stage || !pointer) return
          const oldScale = viewport.zoom
          const mousePoint = { x: (pointer.x - viewport.x) / oldScale, y: (pointer.y - viewport.y) / oldScale }
          const direction = event.evt.deltaY > 0 ? -1 : 1
          const nextZoom = Math.min(1.5, Math.max(0.35, oldScale + direction * 0.08))
          onViewportChange({ zoom: Number(nextZoom.toFixed(2)), x: pointer.x - mousePoint.x * nextZoom, y: pointer.y - mousePoint.y * nextZoom, mode: 'custom' })
        }}
      >
        <Layer ref={backdropLayerRef} listening={false}>
          <Rect x={ARTBOARD.x} y={ARTBOARD.y} width={ARTBOARD.width} height={ARTBOARD.height} fill="#fbfaf7" shadowColor="#4b4e49" shadowOpacity={0.18} shadowBlur={32} shadowOffsetY={14} />
        </Layer>
        <Layer clipX={ARTBOARD.x} clipY={ARTBOARD.y} clipWidth={ARTBOARD.width} clipHeight={ARTBOARD.height}>
          <Rect x={ARTBOARD.x} y={ARTBOARD.y} width={ARTBOARD.width} height={ARTBOARD.height} fill="#fbfaf7" listening={false} />
          {nodes.filter((node) => node.visible !== false).map((node) => node.type === 'image' ? (
            <ImageNode
              key={node.id}
              node={node}
              canDrag={!readOnly && tool === 'select' && node.id !== 'scene-background' && node.locked !== true}
              onSelect={(event) => selectNode(node.id, event)}
              onDragStart={(event) => handleDragStart(node.id, event)}
              onDragMove={(event) => handleDragMove(node.id, event)}
              onDragEnd={(event) => handleDragEnd(node.id, event)}
              onRef={rememberNodeRef}
              onImageState={rememberImageState}
            />
          ) : (
            <TextNode
              key={node.id}
              node={node}
              canDrag={!readOnly && tool === 'select' && node.locked !== true}
              onSelect={(event) => selectNode(node.id, event)}
              onDragStart={(event) => handleDragStart(node.id, event)}
              onDragMove={(event) => handleDragMove(node.id, event)}
              onDragEnd={(event) => handleDragEnd(node.id, event)}
              onRef={rememberNodeRef}
            />
          ))}
        </Layer>
        <Layer ref={overlayLayerRef} listening={!readOnly && tool === 'select'}>
          <Rect x={ARTBOARD.x} y={ARTBOARD.y} width={ARTBOARD.width} height={ARTBOARD.height} stroke="#c8c9c3" strokeWidth={1} listening={false} />
          {guideLines.map((points, index) => <Line key={index} points={points} stroke="#3a8f82" opacity={0.34} dash={[7, 8]} strokeWidth={1} listening={false} />)}
          {snapGuides.vertical !== undefined && <Line points={[snapGuides.vertical, ARTBOARD.y - 20, snapGuides.vertical, ARTBOARD.y + ARTBOARD.height + 20]} stroke="#d06262" strokeWidth={1} dash={[4, 4]} listening={false} />}
          {snapGuides.horizontal !== undefined && <Line points={[ARTBOARD.x - 20, snapGuides.horizontal, ARTBOARD.x + ARTBOARD.width + 20, snapGuides.horizontal]} stroke="#d06262" strokeWidth={1} dash={[4, 4]} listening={false} />}
          {marquee && <Rect {...marquee} fill="rgba(19,103,93,.09)" stroke="#13675d" strokeWidth={1} dash={[5, 4]} listening={false} />}
          <Text x={ARTBOARD.x} y={ARTBOARD.y - 28} text={`${artboardLabel} · 1024 × 1024`} fontSize={13} fill="#60635e" listening={false} />
          <Transformer
            ref={transformerRef}
            rotateEnabled
            keepRatio={Boolean(singleSelected?.type === 'image')}
            enabledAnchors={singleSelected?.type === 'text' ? ['middle-left', 'middle-right'] : undefined}
            borderStroke="#13675d"
            borderStrokeWidth={1.5}
            anchorFill="#ffffff"
            anchorStroke="#13675d"
            anchorSize={10}
            anchorCornerRadius={2}
            boundBoxFunc={(oldBox, newBox) => (newBox.width < 24 || newBox.height < 24 ? oldBox : newBox)}
            onTransformEnd={transformSelection}
          />
        </Layer>
      </Stage>
    </div>
  )
})
