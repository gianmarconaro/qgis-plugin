from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt.QtGui import QIntValidator
from qgis.PyQt.QtWidgets import (
    QInputDialog,
    QLineEdit,
    QDialog,
    QVBoxLayout,
    QLabel,
    QCheckBox,
    QDialogButtonBox,
    QComboBox,
    QCompleter,
)
from qgis.core import (
    QgsProject,
    QgsGeometry,
    QgsPointXY,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsMapLayer,
)

from qgis.utils import iface
from qgis.core import Qgis

from .resources import *

from .analysis_functions import *
from .gtfs_db import Database

from collections import defaultdict
import networkx as nx
import osmnx as ox
import os


def get_inputs_from_dialog_nearby_stops_paths(inputs):
    """Nearby stops analysis inputs"""

    dialog = QDialog()
    dialog.setWindowTitle("Nearby Stops Paths Analysis")

    layout = QVBoxLayout()
    dialog.setFixedSize(400, 175)

    label = QLabel("Insert the stop layer you want to analyse")
    layout.addWidget(label)

    # create combo box
    layers = QgsProject.instance().mapLayers()
    vector_layers = []
    active_vector_layers_names = []

    for layer in layers.values():
        if layer.type() == QgsMapLayer.VectorLayer:
            vector_layers.append(layer)

    for layer in vector_layers:
        if layer.geometryType() == QgsWkbTypes.PointGeometry:
            active_vector_layers_names.append(layer.name())

    inputs.points_combo_box = QComboBox()
    inputs.points_combo_box.addItems(active_vector_layers_names)
    inputs.points_combo_box.setPlaceholderText("Points Layer")
    inputs.points_combo_box.setEditable(True)
    inputs.points_combo_box.setMaxVisibleItems(5)

    # define compleater
    compleater = QCompleter(active_vector_layers_names)
    compleater.setCaseSensitivity(Qt.CaseInsensitive)

    inputs.points_combo_box.setCompleter(compleater)
    layout.addWidget(inputs.points_combo_box)

    label = QLabel("Insert the range of the analysis")
    layout.addWidget(label)

    # create the line edit
    inputs.range_line_edit = QLineEdit()
    inputs.range_line_edit.setPlaceholderText("Range (m) [100-2000]")
    inputs.range_line_edit.setValidator(QIntValidator(100, 2000))
    layout.addWidget(inputs.range_line_edit)

    dialog.setLayout(layout)

    # create the button box
    button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    layout.addWidget(button_box)

    result = dialog.exec_()

    if result != QDialog.Accepted:
        return

    if not inputs.range_line_edit.hasAcceptableInput():
        iface.messageBar().pushMessage(
            "Error",
            "Range must be within the range",
            level=Qgis.Critical,
            duration=5,
        )
        return get_inputs_from_dialog_nearby_stops_paths()
    
    points = []
    layer_name = inputs.points_combo_box.currentText()
    points_layer = QgsProject.instance().mapLayersByName(layer_name)[0]
    for feature in points_layer.getFeatures():
        points.append(feature.geometry().asPoint())
    range = inputs.range_line_edit.text()

    # managing errors
    handle_service_area_input_errors(range)

    return points, int(range)


def handle_service_area_input_errors(range):
    """Manage errors for nearby stops"""

    # check if the range is within the range
    if range == "":
        iface.messageBar().pushMessage(
            "Error",
            "Range and time must be within the range",
            level=Qgis.Critical,
            duration=5,
        )
        return get_inputs_from_dialog_nearby_stops_paths()


def start_nearby_stops_paths_analysis(
    inputs, starting_dialog: QInputDialog, _, G_walk: nx.MultiDiGraph
):
    """Start the nearby stops analysis"""
    if starting_dialog:
        starting_dialog.close()

    try:
        points, range = get_inputs_from_dialog_nearby_stops_paths(
            inputs
        )
    except TypeError:
        return

    crs = QgsProject.instance().crs()

    nearby_stops_paths_analysis_operations(
        inputs, crs, points, range, G_walk
    )


def find_intersections(inputs, i):
    """Find the intersections between the drive graph and the shortest paths"""

    if i == 1:
        directory = inputs._path + "/intersections"
        if not os.path.exists(directory):
            os.makedirs(directory)

    LAYER_NAME_DRIVE_GRAPH = "drive_graph"
    LAYER_NAME_SHORTEST_PATH = f"shortest_paths_{i}"

    project = QgsProject.instance()
    drive_graph_layer = project.mapLayersByName(LAYER_NAME_DRIVE_GRAPH)[0]
    service_area_layer = project.mapLayersByName(LAYER_NAME_SHORTEST_PATH)[0]

    intersections_dict = defaultdict(list)

    # create a spatial index for the drive graph layer (the bigger one)
    drive_graph_index = QgsSpatialIndex(drive_graph_layer.getFeatures())

    for service_area_feature in service_area_layer.getFeatures():
        service_area_geometry = service_area_feature.geometry()
        intersecting_drive_graph_ids = drive_graph_index.intersects(
            service_area_geometry.boundingBox()
        )

        for drive_graph_id in intersecting_drive_graph_ids:
            drive_graph_feature = drive_graph_layer.getFeature(drive_graph_id)
            drive_graph_geometry = drive_graph_feature.geometry()

            if service_area_geometry.intersects(drive_graph_geometry):
                osmid = drive_graph_feature["osmid"]
                street_name = drive_graph_feature["name"]

                if osmid not in intersections_dict:
                    intersections_dict[(osmid, street_name)] = 1

                intersections_dict[(osmid, street_name)] += 1

    # write the result into a txt file
    with open(inputs._path + f"/intersections/intersections_{i}.txt", "w") as outfile:
        for (id, street_name), occurrences in intersections_dict.items():
            outfile.write(f"{id} - {street_name}: {occurrences}\n")


def nearby_stops_paths_analysis_operations(
    inputs,
    crs: QgsCoordinateReferenceSystem,
    points: list,
    range: int,
    G_walk: nx.MultiDiGraph,
):
    """Operations for nearby stops analysis"""
    # create a spatial index for the stops layer (the bigger one)
    stops_layer = QgsProject.instance().mapLayersByName("stops")[0]
    stops_index = QgsSpatialIndex(stops_layer.getFeatures())

    nearest_stop_ids= []
    for point in points:
        nearest_stop = stops_index.nearestNeighbor(point, 1)[0]
        stop_feature = stops_layer.getFeature(nearest_stop)
        current_stop_id = stop_feature["ID"]
        current_stop_point = stop_feature.geometry().asPoint()
        nearest_stop_ids.append([current_stop_id, current_stop_point])     

    for i, stop in enumerate(nearest_stop_ids, 1):
        current_stop_id = stop[0]
        current_stop_name = stop[1]
        x_coord = stop[1][0]
        y_coord = stop[1][1]

        starting_point = QgsPointXY(x_coord, y_coord)
        starting_point_geometry = QgsGeometry.fromPointXY(starting_point)

        fields = QgsFields()
        fields.append(QgsField("ID", QVariant.String))
        fields.append(QgsField("Stop_name", QVariant.String))

        create_and_load_layer_starting_point(crs, fields, starting_point_geometry, i, True)

        current_stop_transports = Database().select_transports_by_stop_id(current_stop_id)
        current_stop_transports_list = [
            transport[0] for transport in current_stop_transports
        ]

        circular_buffer = create_and_load_layer_circular_buffer(
            crs, starting_point_geometry, stops_layer, range, i
        )

        selected_stops = create_and_load_layer_selected_stops(
            crs,
            fields,
            stops_layer,
            circular_buffer,
            current_stop_transports_list,
            current_stop_id,
            i,
        )

        starting_point_nearest_node = ox.nearest_nodes(G_walk, x_coord, y_coord)

        starting_stop_info = [
            current_stop_id,
            current_stop_name,
            starting_point_nearest_node,
        ]

        if selected_stops:
            create_and_load_layer_shortest_paths(
                crs, selected_stops, starting_stop_info, G_walk, i
            )

            find_intersections(inputs, i)
