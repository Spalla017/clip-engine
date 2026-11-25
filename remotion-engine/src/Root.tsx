import React from "react";
import { Composition } from "remotion";
import { ClassicViralShort } from "./ClassicViralShort";
import { SplitAndZoomShort } from "./SplitAndZoomShort";
import { StreamerShort } from "./StreamerShort";
import { MapPin, MapPinSchema } from "./MapPin";
import { ShortPropsSchema, type ShortProps } from "./types";

const defaults = ShortPropsSchema.parse({ durationInSeconds: 30 });

const meta = ({ props }: { props: ShortProps }) => ({
  durationInFrames: Math.max(1, Math.ceil(props.durationInSeconds * props.fps)),
  fps: props.fps,
  width: 1080,
  height: 1920,
});

export const Root: React.FC = () => (
  <>
    <Composition
      id="ClassicViralShort"
      component={ClassicViralShort}
      width={1080}
      height={1920}
      fps={60}
      durationInFrames={1800}
      schema={ShortPropsSchema}
      defaultProps={defaults}
      calculateMetadata={meta}
    />
    <Composition
      id="StreamerShort"
      component={StreamerShort}
      width={1080}
      height={1920}
      fps={60}
      durationInFrames={1800}
      schema={ShortPropsSchema}
      defaultProps={defaults}
      calculateMetadata={meta}
    />
    <Composition
      id="SplitAndZoomShort"
      component={SplitAndZoomShort}
      width={1080}
      height={1920}
      fps={60}
      durationInFrames={1800}
      schema={ShortPropsSchema}
      defaultProps={defaults}
      calculateMetadata={meta}
    />
    <Composition
      id="MapPin"
      component={MapPin}
      width={1080}
      height={1920}
      fps={60}
      durationInFrames={156}
      schema={MapPinSchema}
      defaultProps={{ map: "maps/gta6_map.jpg", mapW: 1000, mapH: 1400, pins: [{ x: 0.5, y: 0.5 }], active: 0, label: "TEST",
        region: "", accent: "#FF3EA5", route: "#E8FF3B", zoom: 2.1, minZoom: 1, durationInSeconds: 2.6 }}
      calculateMetadata={({ props }) => ({ durationInFrames: Math.ceil(props.durationInSeconds * 60), fps: 60, width: 1080, height: 1920 })}
    />
  </>
);
