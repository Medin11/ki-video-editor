// Meldet die Komposition "Edit" an. Länge und Bildrate kommen aus den props,
// die Python beim Rendern mitgibt (--props=props.json).
import React from "react";
import { Composition } from "remotion";
import { Edit, EditProps } from "./Edit";

const defaults: EditProps = {
  video: "cut.mp4",
  videoWidth: 1080,
  videoHeight: 1920,
  fps: 30,
  durationInFrames: 300,
  theme: "coral",
  captions: null,
  scenes: [],
};

export const Root: React.FC = () => (
  <Composition
    id="Edit"
    component={Edit as any}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={300}
    defaultProps={defaults}
    calculateMetadata={({ props }) => ({
      fps: props.fps,
      durationInFrames: Math.max(1, props.durationInFrames),
    })}
  />
);
