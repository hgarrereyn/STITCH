# SGraph Viewer

A simple HTML/JavaScript application for visualizing SGraph JSON files using Dagre-D3 for automatic graph layout.

## Usage

1. Open `index.html` in a web browser.
2. Either drag and drop a JSON file onto the drop area or click the "Select a file" button to choose a file.
3. The application will render the SGraph as an interactive visualization.

## Features

- Drag and drop file selection
- Interactive graph visualization using Dagre-D3 for automatic layout
- Node selection to view detailed information
- Smooth panning and zooming with mouse control
- Reset view functionality
- Export as SVG

## File Format

The viewer expects a JSON file in the SGraph format, which includes:

```json
{
  "nodes": [
    {
      "func_idx": 0,
      "inputs": [
        {
          "other_idx": 0,
          "other_conn_idx": 0,
          "object_idx": 0,
          "attrs": []
        }
      ],
      "outputs": [
        {
          "other_idx": 1,
          "other_conn_idx": 0,
          "object_idx": 0,
          "attrs": []
        }
      ]
    }
  ]
}
```

## Structure

The application consists of:

- `index.html`: Main HTML page with the user interface
- `styles.css`: CSS styles for the application
- `script.js`: JavaScript code that handles file loading and visualization

## Dependencies

The application uses the following external libraries:

- [D3.js](https://d3js.org/) - For data visualization
- [Dagre-D3](https://github.com/dagrejs/dagre-d3) - For automatic graph layout 