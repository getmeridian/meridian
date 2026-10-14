import { Composition } from "remotion";
import { FPS, HEIGHT, WIDTH } from "./theme";
import { totalFrames } from "./timeline";
import { Video } from "./Video";

export function Root() {
  return <Composition id="Setup" component={Video} durationInFrames={totalFrames} fps={FPS} width={WIDTH} height={HEIGHT} />;
}
