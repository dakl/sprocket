"""What replay carries from one feature to the next, and the bodies it finds.

Its own module, with no op imports, because every op takes a `Context` and
replay imports every op.
"""

import dataclasses
from dataclasses import dataclass, field
from typing import cast

from build123d import Compound

from sprocket_engine.model import (
    AxisFeature,
    JSONObject,
    PlaneFeature,
    SketchFeature,
    sketch_feature,
)


@dataclass
class Body:
    """One body the replayed features make: a solid feature with no base, and everything built on it.

    `id` is the feature that started it and `tip` its latest feature, whose
    result is `shape`. The last four fields are the document's settings for
    the body; `find_bodies` leaves them at their defaults and `with_settings`
    fills them in.
    """

    id: str
    tip: str
    shape: Compound
    reference: bool
    name: str
    color: str | None = None
    material: str | None = None
    visible: bool = True

    def with_settings(self, settings: JSONObject) -> "Body":
        """This body with the document's settings for it: name, colour, finish, visibility."""
        name, color, material, visible = (
            settings.get("name"),
            settings.get("color"),
            settings.get("material"),
            settings.get("visible"),
        )
        return dataclasses.replace(
            self,
            name=name if isinstance(name, str) else self.id,
            color=color if isinstance(color, str) else None,
            material=material if isinstance(material, str) else None,
            visible=visible if isinstance(visible, bool) else True,
        )


@dataclass
class Context:
    """Carries each feature's result forward so later features can reference it."""

    results: dict[str, Compound] = field(default_factory=dict[str, Compound])
    # The features replayed so far, by id. An extrude that picks a region needs
    # the sketch's entities, which the sketch's shape no longer carries.
    features: dict[str, JSONObject] = field(default_factory=dict[str, JSONObject])
    # The document's bodies after the replay, see find_bodies.
    bodies: list[Body] = field(default_factory=list[Body])

    def sketch(self, feature_id: str) -> SketchFeature | None:
        """The sketch feature replayed as `feature_id`, or None if that is not a sketch."""
        feature = self.features.get(feature_id)
        return None if feature is None else sketch_feature(feature)

    def plane_feature(self, feature_id: str) -> PlaneFeature | None:
        """The plane feature replayed as `feature_id`, or None if that is not one."""
        feature = self.features.get(feature_id)
        if feature is None or feature.get("type") != "plane":
            return None
        return cast("PlaneFeature", cast("object", feature))

    def axis_feature(self, feature_id: str) -> AxisFeature | None:
        """The axis feature replayed as `feature_id`, or None if that is not one."""
        feature = self.features.get(feature_id)
        if feature is None or feature.get("type") != "axis":
            return None
        return cast("AxisFeature", cast("object", feature))
