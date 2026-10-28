"""Vocabulary of the IfcUSD extension for robot and simulation data.

Names and meanings shared by both directions: ``ifcusd to-ifc`` writes these
property sets into IFC, and ``ifcusd convert`` turns ``IfcUSD_Physics`` back
into ``UsdPhysics`` schemas. The specification is on the website under
Reference > IFC extension for robot data.
"""

from __future__ import annotations

PHYSICS_SET = "IfcUSD_Physics"
SIMULATION_SET = "IfcUSD_Simulation"
LIBRARY_NAME = "IfcUSD Extension"
SEMANTICS_SOURCE = "OpenUSD UsdSemantics"

SET_DESCRIPTIONS = {
    PHYSICS_SET: "Physical simulation properties from OpenUSD UsdPhysics, written by IfcUSD.",
    SIMULATION_SET: "Motion recorded in an OpenUSD simulation (time-sampled transforms), written by IfcUSD.",
}
PROPERTY_DESCRIPTIONS = {
    PHYSICS_SET: {
        "Mass": "Mass (UsdPhysics.MassAPI); the sum over the object's bodies when not authored on the object.",
        "Density": "Mass density (UsdPhysics.MassAPI, or the bound physics material).",
        "CenterOfMass": "Centre of mass X, Y, Z in the object's placement frame.",
        "RigidBodyEnabled": "The object is a dynamic rigid body (UsdPhysics.RigidBodyAPI).",
        "KinematicEnabled": "The rigid body is moved kinematically, not by the solver.",
        "CollisionEnabled": "The object, or a part of it, collides (UsdPhysics.CollisionAPI).",
        "CollisionApproximation": "Collision shape approximation (UsdPhysics.MeshCollisionAPI), e.g. convexHull.",
        "StaticFriction": "Static friction coefficient of the bound physics material.",
        "DynamicFriction": "Dynamic friction coefficient of the bound physics material.",
        "Restitution": "Coefficient of restitution of the bound physics material.",
        "Articulated": "The object is an articulation (UsdPhysics.ArticulationRootAPI), typically a robot.",
        "JointCount": "Number of physics joints in the object.",
    },
    SIMULATION_SET: {
        "Trajectory": "Position X, Y, Z (project length unit) and orientation quaternion W, X, Y, Z in the project "
        "coordinate system at every USD time sample.",
        "Duration": "Time between the first and the last trajectory sample.",
        "PathLength": "Distance travelled along the trajectory.",
    },
}

# Key in a prim's ``customData.ifc`` listing the UsdPhysics schemas ``ifcusd convert``
# applied for IfcUSD_Physics (collision schemas: on the prim's meshes). ``ifcusd to-ifc``
# reads a listed schema that is no longer applied as removed in the simulation.
PHYSICS_SCHEMAS_KEY = "physicsSchemas"

# Values of UsdPhysics.MeshCollisionAPI's physics:approximation.
COLLISION_APPROXIMATIONS = (
    "none",
    "convexDecomposition",
    "convexHull",
    "boundingSphere",
    "boundingCube",
    "meshSimplification",
)
