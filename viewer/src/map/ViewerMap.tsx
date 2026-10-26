import 'maplibre-gl/dist/maplibre-gl.css'
import type { FeatureCollection } from 'geojson'
import type { Map as MapLibreMap, MapLibreEvent } from 'maplibre-gl'
import { Fragment, useEffect, useEffectEvent, useRef, useState } from 'react'
import {
  AttributionControl,
  Layer,
  Map,
  Marker,
  type MapMouseEvent,
  MapProvider,
  NavigationControl,
  ScaleControl,
  Source,
  type ViewStateChangeEvent,
  useMap,
} from 'react-map-gl/maplibre'
import { adjustmentKey, type ResultLayer, type Scene } from '../data/manifest'
import {
  type Adjustment,
  extentCentre,
  extentCorners,
  type LngLatCorners,
  offsetAdjustment,
  placeCorners,
} from '../geo/adjustment'
import { type LngLat, lngLatBounds, offsetBetween } from '../geo/coordinates'
import {
  formatDistanceLabel,
  type Measurement,
  measurementDistance,
  midpoint,
} from '../geo/measurements'
import { useAdjustmentActions } from '../state/adjustment-store'
import {
  useDragAnchor,
  useHideResults,
  useMapToolActions,
  useMeasureStart,
  useMoveMode,
} from '../state/map-tool-store'
import { useIsMagnified, useMapLoaded, useMapViewActions } from '../state/map-view-store'
import { useMeasurementActions, useMeasurements } from '../state/measurement-store'
import { readUrlMap, writeUrlMap } from '../state/url-selection'
import { useShowMarkers } from '../state/view-store'
import type { Background } from './backgrounds'

const MAP_ID = 'mainMap'
const MEASURE_SOURCE_ID = 'measure'
const MEASURE_LINE_ID = 'measure-line'
const MEASURE_POINTS_ID = 'measure-points'
const MEASURE_LAYER_IDS = [MEASURE_LINE_ID, MEASURE_POINTS_ID]

export type ViewerMapProps = {
  scene: Scene
  /** Prefix for tile image paths, with a trailing slash. */
  dataBaseUrl: string
  activeLayer: ResultLayer | undefined
  compareLayer: ResultLayer | undefined
  /** 0 to 100. */
  opacity: number
  /** 0 to 100. */
  compareOpacity: number
  adjustment: Adjustment
  background: Background
}

export function ViewerMap(props: ViewerMapProps) {
  return (
    <MapProvider>
      <MapCanvas {...props} />
      <FlyToScene scene={props.scene} adjustment={props.adjustment} />
    </MapProvider>
  )
}

function toLngLat(event: MapMouseEvent): LngLat {
  return [event.lngLat.lng, event.lngLat.lat]
}

const RED = '#dc2626'

function measureGeoJson(measurements: Measurement[], start: LngLat | null): FeatureCollection {
  const features: FeatureCollection['features'] = []
  const points = [...measurements.flatMap(({ a, b }) => [a, b]), ...(start ? [start] : [])]
  for (const point of points) {
    features.push({
      type: 'Feature',
      properties: {},
      geometry: { type: 'Point', coordinates: point },
    })
  }
  for (const { a, b } of measurements) {
    features.push({
      type: 'Feature',
      properties: {},
      geometry: { type: 'LineString', coordinates: [a, b] },
    })
  }
  return { type: 'FeatureCollection', features }
}

function MapCanvas({
  scene,
  dataBaseUrl,
  activeLayer,
  compareLayer,
  opacity,
  compareOpacity,
  adjustment,
  background,
}: ViewerMapProps) {
  // A `map` param in the URL wins over the scene's default view. Read once, uncontrolled afterwards.
  const [initialView] = useState(() => {
    const fromUrl = readUrlMap(window.location.search)
    return fromUrl
      ? { longitude: fromUrl.lng, latitude: fromUrl.lat, zoom: fromUrl.zoom }
      : { longitude: scene.origin[0], latitude: scene.origin[1], zoom: 17 }
  })
  const moveMode = useMoveMode()
  const dragAnchor = useDragAnchor()
  const measureStart = useMeasureStart()
  const measurements = useMeasurements(scene.id)
  const { addMeasurement } = useMeasurementActions()
  const showMarkers = useShowMarkers()
  const hideResults = useHideResults()
  const tools = useMapToolActions()
  const view = useMapViewActions()
  const { updateAdjustment } = useAdjustmentActions()

  function handleLoad(event: MapLibreEvent) {
    view.setView(event.target.getZoom(), event.target.getCenter().lat)
    view.markLoaded()
  }

  function handleMove(event: ViewStateChangeEvent) {
    view.setView(event.viewState.zoom, event.viewState.latitude)
  }

  function handleMoveEnd(event: ViewStateChangeEvent) {
    const { latitude, longitude, zoom } = event.viewState
    writeUrlMap({ zoom, lat: latitude, lng: longitude })
  }

  function handleMouseDown(event: MapMouseEvent) {
    if (moveMode) tools.setDragAnchor(toLngLat(event))
  }

  function handleMouseMove(event: MapMouseEvent) {
    if (!moveMode || dragAnchor === null) return
    const current = toLngLat(event)
    const [dx, dy] = offsetBetween(scene.origin, dragAnchor, current)
    updateAdjustment(adjustmentKey(scene), (value) => offsetAdjustment(value, dx, dy))
    tools.setDragAnchor(current)
  }

  function handleClick(event: MapMouseEvent) {
    if (moveMode) return
    const point = toLngLat(event)
    if (measureStart === null) {
      tools.setMeasureStart(point)
      return
    }
    addMeasurement(scene.id, {
      layerId: activeLayer?.id ?? '',
      a: measureStart,
      b: point,
      zoom: event.target.getZoom(),
    })
    tools.clearMeasure()
  }

  return (
    <Map
      id={MAP_ID}
      initialViewState={initialView}
      mapStyle={background.style}
      style={{ width: '100%', height: '100%' }}
      maxZoom={24}
      dragRotate={false}
      dragPan={!moveMode}
      cursor={moveMode ? 'move' : 'crosshair'}
      attributionControl={false}
      onLoad={handleLoad}
      onMove={handleMove}
      onMoveEnd={handleMoveEnd}
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={() => tools.setDragAnchor(null)}
      onMouseLeave={() => tools.setDragAnchor(null)}
      onClick={handleClick}
    >
      {/* Switching the basemap replaces the whole style; keying on it re-adds our sources and layers. */}
      <Fragment key={background.id}>
        {compareLayer && (
          <ResultLayerTiles
            prefix="compare"
            scene={scene}
            dataBaseUrl={dataBaseUrl}
            layer={compareLayer}
            adjustment={adjustment}
            opacity={compareOpacity / 100}
            hidden={hideResults}
          />
        )}
        {activeLayer && (
          <ResultLayerTiles
            prefix="active"
            scene={scene}
            dataBaseUrl={dataBaseUrl}
            layer={activeLayer}
            adjustment={adjustment}
            opacity={opacity / 100}
            hidden={hideResults}
          />
        )}

        <Source
          id={MEASURE_SOURCE_ID}
          type="geojson"
          data={measureGeoJson(measurements, measureStart)}
        />
        <Layer
          id={MEASURE_LINE_ID}
          type="line"
          source={MEASURE_SOURCE_ID}
          filter={['==', ['geometry-type'], 'LineString']}
          paint={{ 'line-color': RED, 'line-width': 2.5 }}
        />
        <Layer
          id={MEASURE_POINTS_ID}
          type="circle"
          source={MEASURE_SOURCE_ID}
          filter={['==', ['geometry-type'], 'Point']}
          paint={{
            'circle-radius': 5,
            'circle-color': RED,
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2,
          }}
        />
        <KeepOnTop layerIds={MEASURE_LAYER_IDS} />
      </Fragment>

      {measurements.map((measurement) => (
        <DistanceLabel key={measurement.id} measurement={measurement} />
      ))}
      {showMarkers &&
        (scene.markers ?? []).map((marker) => (
          <Marker
            key={`${marker.position.join()}-${marker.label}`}
            longitude={marker.position[0]}
            latitude={marker.position[1]}
            style={{ pointerEvents: 'none' }}
          >
            <SceneMarkerSymbol label={marker.label} />
          </Marker>
        ))}

      <ScaleControl position="bottom-left" unit="metric" />
      <NavigationControl position="bottom-right" showCompass={false} />
      <AttributionControl compact customAttribution={background.attribution} />
    </Map>
  )
}

/**
 * The distance as a pixel-sized HTML label on the middle of the line: it stays readable at every
 * zoom and needs no glyph font. Pointer events are off, so clicks reach the map underneath.
 */
function DistanceLabel({ measurement }: { measurement: Measurement }) {
  const [lng, lat] = midpoint(measurement.a, measurement.b)
  return (
    <Marker longitude={lng} latitude={lat} offset={[0, -14]} style={{ pointerEvents: 'none' }}>
      <span className="rounded border border-red-600 bg-white/95 px-1.5 py-0.5 text-sm font-semibold whitespace-nowrap text-red-800 tabular-nums shadow">
        {formatDistanceLabel(measurementDistance(measurement))}
      </span>
    </Marker>
  )
}

/** Red ring with the label beside it. HTML on top of the canvas, so it is always above the image tiles. */
function SceneMarkerSymbol({ label }: { label: string }) {
  return (
    <div className="relative size-5 rounded-full border-[3px] border-red-600 bg-red-600/25">
      <span className="absolute top-1/2 left-full ml-1.5 -translate-y-1/2 text-xs font-semibold whitespace-nowrap text-red-800 [text-shadow:0_0_3px_#fff,0_0_3px_#fff,0_0_3px_#fff,0_0_3px_#fff]">
        {label}
      </span>
    </div>
  )
}

/** True when the last layers of the style are exactly `ids`, in that order. */
function isOnTop(map: MapLibreMap, ids: string[]): boolean {
  const layers = map.getStyle().layers
  return ids.every((id, index) => layers[layers.length - ids.length + index]?.id === id)
}

/**
 * Result image layers are added later than the measure layers (a new result layer lands on top of
 * the style), so keep the line and points above them: move them to the top whenever the style changes.
 */
function KeepOnTop({ layerIds }: { layerIds: string[] }) {
  const { current: reactMap } = useMap()

  useEffect(
    function keepLayersOnTop() {
      const map = reactMap?.getMap()
      if (!map) return
      function moveToTop() {
        if (!map) return
        try {
          const present = layerIds.filter((id) => map.getLayer(id))
          if (present.length === 0 || isOnTop(map, present)) return
          for (const id of present) map.moveLayer(id)
        } catch {
          // The style is being replaced; the next styledata event tries again.
        }
      }
      moveToTop()
      map.on('styledata', moveToTop)
      return () => {
        map.off('styledata', moveToTop)
      }
    },
    [reactMap, layerIds],
  )

  return null
}

type ResultLayerTilesProps = {
  prefix: string
  scene: Scene
  dataBaseUrl: string
  layer: ResultLayer
  adjustment: Adjustment
  /** 0 to 1. */
  opacity: number
  hidden: boolean
}

function ResultLayerTiles({
  prefix,
  scene,
  dataBaseUrl,
  layer,
  adjustment,
  opacity,
  hidden,
}: ResultLayerTilesProps) {
  const centre = extentCentre(scene.extentMeters)
  return layer.tiles.map((tile, index) => {
    const sourceId = `${prefix}-${layer.id}-${index}`
    return (
      <RasterTile
        key={sourceId}
        sourceId={sourceId}
        url={`${dataBaseUrl}${tile.image}`}
        coordinates={placeCorners(tile.corners, scene.origin, centre, adjustment)}
        opacity={opacity}
        hidden={hidden}
        metresPerPixel={layer.metersPerPixel}
      />
    )
  })
}

type RasterTileProps = {
  sourceId: string
  url: string
  coordinates: LngLatCorners
  opacity: number
  hidden: boolean
  metresPerPixel: number
}

/** One georeferenced image chunk. Each tile is its own source and layer, so the layer can be removed on its own. */
function RasterTile({
  sourceId,
  url,
  coordinates,
  opacity,
  hidden,
  metresPerPixel,
}: RasterTileProps) {
  const magnified = useIsMagnified(metresPerPixel)
  return (
    <>
      <Source id={sourceId} type="image" url={url} coordinates={coordinates} />
      <Layer
        id={`${sourceId}-raster`}
        type="raster"
        source={sourceId}
        layout={{ visibility: hidden ? 'none' : 'visible' }}
        paint={{
          'raster-opacity': opacity,
          'raster-resampling': magnified ? 'nearest' : 'linear',
          'raster-fade-duration': 0,
        }}
      />
    </>
  )
}

/**
 * Fits the camera to the scene whenever the selected scene changes, once the map has loaded.
 * The first fit is skipped when the URL already carries a `map` param, which wins over the default.
 */
function FlyToScene({ scene, adjustment }: { scene: Scene; adjustment: Adjustment }) {
  const maps = useMap()
  const map = maps[MAP_ID]
  const loaded = useMapLoaded()
  const fittedSceneId = useRef<string | null>(readUrlMap(window.location.search) ? scene.id : null)

  const flyToScene = useEffectEvent(function flyToScene() {
    if (!map) return
    const centre = extentCentre(scene.extentMeters)
    const corners = placeCorners(
      extentCorners(scene.extentMeters),
      scene.origin,
      centre,
      adjustment,
    )
    const [southWest, northEast] = lngLatBounds(corners)
    map.fitBounds([southWest, northEast], { padding: 60, duration: 800 })
  })

  useEffect(
    function flyToSelectedScene() {
      if (!loaded || fittedSceneId.current === scene.id) return
      fittedSceneId.current = scene.id
      flyToScene()
    },
    [scene.id, loaded],
  )

  return null
}
