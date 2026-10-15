import { Composition, Still } from "remotion";
import { FPS, HEIGHT, WIDTH } from "./theme";
import { Thumbnail } from "./Thumbnail";
import { totalFrames } from "./timeline";
import { Video } from "./Video";

export function Root() {
  return (
    <>
      <Composition
        id="Setup"
        component={Video}
        defaultProps={{ lang: "en" as const }}
        durationInFrames={totalFrames("en")}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
      />
      <Composition
        id="SetupRu"
        component={Video}
        defaultProps={{ lang: "ru" as const }}
        durationInFrames={totalFrames("ru")}
        fps={FPS}
        width={WIDTH}
        height={HEIGHT}
      />
      <Still
        id="Thumbnail"
        component={Thumbnail}
        defaultProps={{ lang: "en" as const }}
        width={WIDTH}
        height={HEIGHT}
      />
      <Still
        id="ThumbnailRu"
        component={Thumbnail}
        defaultProps={{ lang: "ru" as const }}
        width={WIDTH}
        height={HEIGHT}
      />
    </>
  );
}
