import React, { useState, useEffect, useRef, createContext, useContext } from 'react';
import { Box, TextField, Grid, List, ListItem, ListItemButton, ListItemText, Typography, Tooltip, IconButton } from '@mui/material';
import { Close as CloseIcon } from '@mui/icons-material';
import ReactFlow, { Background, Controls, useNodesState, useEdgesState, Handle, Position, useReactFlow } from 'reactflow';
import 'reactflow/dist/style.css';
import axios from 'axios';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { coy } from 'react-syntax-highlighter/dist/esm/styles/prism';

const DEFAULT_PORT = process.env.REACT_APP_DEFAULT_LOG_PORT || '3000';

// Pastel colors for types (reused from Schema.js)
const PASTEL_COLORS = [
  '#FFB3BA', '#BAFFC9', '#BAE1FF', '#FFFFBA', '#E8BAFF', 
  '#FFD9BA', '#B3FFE5', '#FFB3E6', '#B3ECFF', '#E5FFB3',
];

const getTypeColor = (idx) => {
  return PASTEL_COLORS[idx % PASTEL_COLORS.length];
};

// Create context for template state
const TemplateContext = createContext();

// Import TemplateCode component from Schema.js
const TemplateCode = ({ code, inputs, outputs, typeNames }) => {
  const TypeList = ({ types, prefix }) => (
    <Box sx={{ 
      borderTop: '1px solid #ddd',
      borderBottom: '1px solid #ddd',
      py: 1,
      backgroundColor: '#fafafa',
      margin: 0
    }}>
      {types.map((typeIdx, idx) => (
        <Box 
          key={idx} 
          sx={{ 
            fontFamily: 'monospace',
            fontSize: '14px',
            py: 0.5,
            px: 2,
            display: 'flex',
            alignItems: 'center'
          }}
        >
          <span style={{ 
            color: prefix === 'i' ? '#4CAF50' : '#2196F3',
            fontWeight: 'bold',
            marginRight: '8px'
          }}>
            ${prefix}{idx}
          </span>
          <span>: {typeNames[typeIdx]}</span>
        </Box>
      ))}
    </Box>
  );

  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Input types */}
      {inputs && inputs.length > 0 && (
        <TypeList types={inputs} prefix="i" />
      )}

      {/* Code section */}
      <Box sx={{ 
        flex: 1,
        position: 'relative',
        overflow: 'hidden',
        minHeight: 0
      }}>
        <SyntaxHighlighter
          language="cpp"
          style={coy}
          customStyle={{ 
            margin: 0,
            height: '100%',
            fontSize: '14px',
            backgroundColor: 'white',
            padding: 0,
            position: 'relative'
          }}
          wrapLines={false}
          showLineNumbers={true}
          lineNumberStyle={{
            minWidth: '2.5em',
            paddingRight: '0.5em',
            textAlign: 'right',
            userSelect: 'none',
            color: '#999',
            borderRight: '1px solid #ddd',
            marginRight: '0.5em',
            position: 'sticky',
            left: 0,
            background: 'white'
          }}
          codeTagProps={{
            style: {
              float: 'left',
              minWidth: '100%',
              paddingLeft: '3.5em',
              boxSizing: 'border-box'
            }
          }}
          PreTag={({ children, ...props }) => (
            <pre {...props} style={{ 
              margin: 0,
              overflow: 'auto',
              float: 'left',
              width: '100%',
              height: '100%'
            }}>
              {children}
            </pre>
          )}
        >
          {code || ''}
        </SyntaxHighlighter>
      </Box>

      {/* Output types */}
      {outputs && outputs.length > 0 && (
        <Box sx={{ 
          marginTop: 'auto', 
          marginBottom: '12px'
        }}>
          <TypeList types={outputs} prefix="o" />
        </Box>
      )}
    </Box>
  );
};

// Update EndpointNode to handle template display
const EndpointNode = ({ data }) => {
  const nodeHeight = 80;
  const circleSize = 16;
  const circleOffset = circleSize / 2;
  const { setHoveredTemplate, hoveredTemplate } = useContext(TemplateContext);

  const handleClick = () => {
    if (hoveredTemplate === data.template) {
      return;
    }
    setHoveredTemplate(data.template);
  };

  return (
    <div 
      onClick={handleClick}
      style={{ 
        position: 'relative',
        padding: '15px',
        paddingTop: '20px',
        paddingBottom: '20px',
        borderRadius: '8px',
        background: hoveredTemplate === data.template ? '#f0f0f0' : 'white',
        border: '1px solid #ddd',
        minWidth: '150px',
        height: `${nodeHeight}px`,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'center',
        cursor: 'pointer',
      }}
    >
      <Typography variant="subtitle2" align="center">{data.name}</Typography>
      
      {/* Input handles */}
      <div style={{ 
        position: 'absolute',
        top: -circleOffset,
        left: 0,
        right: 0,
        display: 'flex',
        justifyContent: 'space-evenly'
      }}>
        {data.inputs.map((typeIdx, idx) => (
          <Tooltip key={`in-${idx}`} title={data.typeNames[typeIdx]} arrow placement="top">
            <div style={{ position: 'relative', width: circleSize, height: circleSize }}>
              <Handle
                type="target"
                position={Position.Top}
                id={`in-${idx}`}
                style={{
                  width: circleSize,
                  height: circleSize,
                  background: getTypeColor(typeIdx),
                  border: '1px solid #666',
                  borderRadius: '50%',
                  position: 'relative',
                  transform: 'none',
                  top: 0,
                  left: 0
                }}
              />
            </div>
          </Tooltip>
        ))}
      </div>

      {/* Output handles */}
      <div style={{ 
        position: 'absolute',
        bottom: -circleOffset,
        left: 0,
        right: 0,
        display: 'flex',
        justifyContent: 'space-evenly'
      }}>
        {data.outputs.map((typeIdx, idx) => (
          <Tooltip key={`out-${idx}`} title={data.typeNames[typeIdx]} arrow placement="bottom">
            <div style={{ position: 'relative', width: circleSize, height: circleSize }}>
              <Handle
                type="source"
                position={Position.Bottom}
                id={`out-${idx}`}
                style={{
                  width: circleSize,
                  height: circleSize,
                  background: getTypeColor(typeIdx),
                  border: '1px solid #666',
                  borderRadius: '50%',
                  position: 'relative',
                  transform: 'none',
                  bottom: 0,
                  left: 0
                }}
              />
            </div>
          </Tooltip>
        ))}
      </div>
    </div>
  );
};

const nodeTypes = {
  endpoint: EndpointNode,
};

// Add custom edge types before the View component
const CustomEdge = ({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  data,
}) => {
  // Calculate the vertical distance between nodes to determine curve intensity
  const deltaY = Math.abs(targetY - sourceY);
  const verticalDistance = Math.min(Math.max(deltaY * 0.5, 50), 150); // Min 50px, max 150px vertical handle

  // Create a nice bezier curve path
  const path = `M${sourceX},${sourceY} 
                C${sourceX},${sourceY + verticalDistance} 
                 ${targetX},${targetY - verticalDistance} 
                 ${targetX},${targetY}`;

  return (
    <>
      {/* Border/shadow path */}
      <path
        d={path}
        fill="none"
        style={{
          stroke: '#666',
          strokeWidth: style.strokeWidth + 2,
          strokeLinecap: 'round',
        }}
      />
      {/* Main colored path */}
      <path
        d={path}
        fill="none"
        style={{
          ...style,
          strokeLinecap: 'round',
        }}
      />
    </>
  );
};

// Update nodeTypes to include the custom edge
const edgeTypes = {
  custom: CustomEdge,
};

function View() {
  const [directory, setDirectory] = useState(() => {
    const saved = localStorage.getItem('lastViewDir');
    return saved || '';
  });
  const [schemaUrl, setSchemaUrl] = useState(() => {
    const saved = localStorage.getItem('lastSchemaUrl');
    return saved || `http://localhost:${DEFAULT_PORT}/schema.json`;
  });
  const [schema, setSchema] = useState(null);
  const [files, setFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [error, setError] = useState(null);
  const reactFlowInstance = useRef(null);
  const [hoveredTemplate, setHoveredTemplate] = useState(null);
  const [panelWidth, setPanelWidth] = useState(400);

  // Calculate min and max widths in pixels
  const minWidth = Math.max(200, window.innerWidth * 0.15);
  const maxWidth = window.innerWidth * 0.6;

  // Update localStorage when paths change
  useEffect(() => {
    localStorage.setItem('lastViewDir', directory);
    localStorage.setItem('lastSchemaUrl', schemaUrl);
  }, [directory, schemaUrl]);

  // Fetch schema
  useEffect(() => {
    const fetchSchema = async () => {
      try {
        // Add cache-busting timestamp
        const cacheBustUrl = `${schemaUrl}${schemaUrl.includes('?') ? '&' : '?'}_t=${Date.now()}`;
        const response = await axios.get(cacheBustUrl);
        setSchema(response.data);
        setError(null);
      } catch (err) {
        console.error('Error fetching schema:', err);
        setError('Error loading schema');
      }
    };

    if (schemaUrl) {
      fetchSchema();
    }
  }, [schemaUrl]);

  // Add auto-refresh
  useEffect(() => {
    const refreshInterval = setInterval(() => {
      if (directory) {
        fetchFiles();
      }
    }, 5000); // Refresh every 5 seconds

    return () => clearInterval(refreshInterval);
  }, [directory]);

  // Extract fetchFiles function from the useEffect to reuse it
  const fetchFiles = async () => {
    try {
      // Helper function to parse directory listing HTML
      const parseDirectoryListing = (html) => {
        const parser = new DOMParser();
        const doc = parser.parseFromString(html, 'text/html');
        const links = Array.from(doc.querySelectorAll('a'));
        return links
          .map(link => link.getAttribute('href'))
          .filter(href => href && !href.startsWith('?') && !href.startsWith('/') && !href.startsWith('.'));
      };

      // Fetch files from directory
      const response = await axios.get(`http://localhost:${DEFAULT_PORT}/${directory}/`);
      const html = response.data;
      const fileList = parseDirectoryListing(html);
      setFiles(fileList);

      setError(null);
    } catch (err) {
      console.error('Error fetching files:', err);
      setError('Error loading directory files');
    }
  };

  // Update the original useEffect to use the extracted function
  useEffect(() => {
    if (directory) {
      fetchFiles();
    }
  }, [directory]);

  // Load and visualize graph when a file is selected
  useEffect(() => {
    const loadGraph = async () => {
      if (!selectedFile || !schema) return;

      try {
        // Direct file access
        const response = await axios.get(`http://localhost:${DEFAULT_PORT}/${selectedFile}`);
        const graph = response.data.graph;
        
        // Convert graph to nodes and edges
        const newNodes = [];
        const newEdges = [];
        const typeNames = Object.fromEntries(
          schema.types.map(type => [type.idx, type.name])
        );

        // Create nodes
        // First, group nodes by layer
        const nodesByLayer = {};
        const nodeConnections = new Map(); // Track connections for each node
        
        // Build connection information
        graph.nodes.forEach((node) => {
          nodesByLayer[node.layer] = nodesByLayer[node.layer] || [];
          nodesByLayer[node.layer].push(node);
          
          const endpoint = schema.endpoints.find(ep => ep.idx === node.typ);
          if (!endpoint) return;

          // Track outgoing and incoming connections with their port positions
          const connections = {
            outgoing: node.out_ref
              .filter(ref => ref.Connected)
              .map((ref, outIdx) => ({
                nodeIdx: ref.Connected.node_idx,
                sourcePort: outIdx,                    // Position in outputs array (left to right)
                targetPort: ref.Connected.conn_idx     // Position in target's inputs array
              })),
            incoming: node.in_ref
              .filter(ref => ref.Connected)
              .map((ref, inIdx) => ({
                nodeIdx: ref.Connected.node_idx,
                sourcePort: ref.Connected.conn_idx,    // Position in source's outputs array
                targetPort: inIdx                      // Position in inputs array (left to right)
              })),
            // Calculate weighted position based on port locations
            getIdealPosition: (positions, nodeWidth = 150) => {
              const connected = [];
              
              // Add positions from incoming connections
              connections.incoming.forEach(conn => {
                const sourcePos = positions.get(conn.nodeIdx);
                if (sourcePos !== undefined) {
                  // Calculate the exact position of the output port on the source node
                  const sourceEndpoint = schema.endpoints.find(ep => ep.idx === 
                    graph.nodes.find(n => n.index === conn.nodeIdx)?.typ);
                  if (sourceEndpoint) {
                    const portOffset = (conn.sourcePort / (sourceEndpoint.outputs.length - 1 || 1)) * nodeWidth;
                    connected.push({
                      position: sourcePos + portOffset - (nodeWidth / 2),
                      weight: 1
                    });
                  }
                }
              });

              // Add positions from outgoing connections
              connections.outgoing.forEach(conn => {
                const targetPos = positions.get(conn.nodeIdx);
                if (targetPos !== undefined) {
                  // Calculate the exact position of the input port on the target node
                  const targetEndpoint = schema.endpoints.find(ep => ep.idx === 
                    graph.nodes.find(n => n.index === conn.nodeIdx)?.typ);
                  if (targetEndpoint) {
                    const portOffset = (conn.targetPort / (targetEndpoint.inputs.length - 1 || 1)) * nodeWidth;
                    connected.push({
                      position: targetPos + portOffset - (nodeWidth / 2),
                      weight: 1
                    });
                  }
                }
              });

              if (connected.length === 0) return null;

              // Calculate weighted average position
              const totalWeight = connected.reduce((sum, c) => sum + c.weight, 0);
              return connected.reduce((sum, c) => sum + c.position * c.weight, 0) / totalWeight;
            }
          };
          nodeConnections.set(node.index, connections);
        });

        // Sort layers to minimize crossings
        const NODE_WIDTH = 150;
        const LAYER_VERTICAL_SPACING = 200;
        const HORIZONTAL_SPACING = 250; // Increased for better spacing
        const nodePositions = new Map();
        
        // Process layers from top to bottom
        const sortedLayers = Object.entries(nodesByLayer)
          .sort(([a], [b]) => parseInt(a) - parseInt(b));

        sortedLayers.forEach(([layer, nodes], layerIndex) => {
          const layerWidth = nodes.length * HORIZONTAL_SPACING;
          const startX = -layerWidth / 2;

          // Sort nodes within the layer based on their connections
          const sortedNodes = [...nodes].sort((a, b) => {
            const aConnections = nodeConnections.get(a.index);
            const bConnections = nodeConnections.get(b.index);
            
            // Get ideal positions based on connected ports
            const aIdealPos = aConnections.getIdealPosition(nodePositions, NODE_WIDTH);
            const bIdealPos = bConnections.getIdealPosition(nodePositions, NODE_WIDTH);
            
            if (aIdealPos === null && bIdealPos === null) return 0;
            if (aIdealPos === null) return 1;
            if (bIdealPos === null) return -1;
            return aIdealPos - bIdealPos;
          });

          // Position nodes and create them
          sortedNodes.forEach((node, index) => {
            const xPos = startX + (index * HORIZONTAL_SPACING);
            nodePositions.set(node.index, xPos);

            const endpoint = schema.endpoints.find(ep => ep.idx === node.typ);
            if (!endpoint) {
              console.error(`Could not find endpoint type ${node.typ} in schema`);
              return;
            }

            newNodes.push({
              id: `node-${node.index}`,
              type: 'endpoint',
              position: { 
                x: xPos,
                y: parseInt(layer) * LAYER_VERTICAL_SPACING
              },
              data: {
                name: endpoint.name,
                inputs: endpoint.inputs,
                outputs: endpoint.outputs,
                typeNames,
                id: `node-${node.index}`,
                template: endpoint.template
              }
            });
          });
        });

        // Create edges from references
        const edgeKeys = new Set(); // Track created edges to avoid duplicates
        graph.nodes.forEach((node) => {
          const sourceEndpoint = schema.endpoints.find(ep => ep.idx === node.typ);
          if (!sourceEndpoint) return;

          // Handle outgoing references only - this ensures each output connects to exactly one input
          node.out_ref.forEach((ref, outIdx) => {
            if (ref.Connected) {
              const targetNode = graph.nodes.find(n => n.index === ref.Connected.node_idx);
              if (!targetNode) return;

              const targetEndpoint = schema.endpoints.find(ep => ep.idx === targetNode.typ);
              if (!targetEndpoint) return;

              // Verify the connection indices are valid
              if (outIdx >= sourceEndpoint.outputs.length || 
                  ref.Connected.conn_idx >= targetEndpoint.inputs.length) {
                console.error(`Invalid connection indices for edge from node ${node.index} to ${ref.Connected.node_idx}`);
                return;
              }

              // Create a unique key for this connection
              const edgeKey = `${node.index}-${outIdx}-${ref.Connected.node_idx}-${ref.Connected.conn_idx}`;
              if (edgeKeys.has(edgeKey)) {
                console.warn(`Duplicate edge detected: ${edgeKey}`);
                return;
              }
              edgeKeys.add(edgeKey);

              const sourceType = sourceEndpoint.outputs[outIdx];
              newEdges.push({
                id: `edge-${edgeKey}`,
                source: `node-${node.index}`,
                target: `node-${ref.Connected.node_idx}`,
                type: 'custom',
                sourceHandle: `out-${outIdx}`,
                targetHandle: `in-${ref.Connected.conn_idx}`,
                style: { 
                  stroke: getTypeColor(sourceType),
                  strokeWidth: 3
                },
                data: {
                  sourceType
                }
              });
            }
          });
        });

        setNodes(newNodes);
        setEdges(newEdges);
        setError(null);
      } catch (err) {
        console.error('Error loading graph:', err);
        setError('Error loading graph file');
      }
    };

    loadGraph();
  }, [selectedFile, schema]);

  // Add effect to trigger smooth fit view when nodes change
  useEffect(() => {
    if (nodes.length > 0 && reactFlowInstance.current) {
      setTimeout(() => {
        reactFlowInstance.current.fitView({
          duration: 800,
          padding: 0.2,
        });
      }, 50); // Small delay to ensure nodes are properly laid out
    }
  }, [nodes]);

  return (
    <TemplateContext.Provider value={{ hoveredTemplate, setHoveredTemplate }}>
      <Box sx={{ flexGrow: 1, height: 'calc(100vh - 48px)', overflow: 'hidden' }}>
        {/* Toolbars */}
        <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
          <Grid container spacing={2}>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Directory Path"
                value={directory}
                onChange={(e) => setDirectory(e.target.value)}
                size="small"
              />
            </Grid>
            <Grid item xs={6}>
              <TextField
                fullWidth
                label="Schema URL"
                value={schemaUrl}
                onChange={(e) => setSchemaUrl(e.target.value)}
                size="small"
              />
            </Grid>
          </Grid>
        </Box>

        {/* Main content */}
        <Box sx={{ 
          flexGrow: 1, 
          display: 'flex', 
          overflow: 'hidden',
          height: 'calc(100% - 80px)' // Subtract toolbar height
        }}>
          {/* Sidebar */}
          <Grid item style={{ 
            width: '200px', 
            borderRight: '1px solid rgba(0, 0, 0, 0.12)', 
            height: '100%', 
            overflow: 'auto' 
          }}>
            {/* Files list */}
            <List dense>
              {files.map((file) => (
                <ListItem key={file} disablePadding>
                  <ListItemButton 
                    selected={selectedFile === `${directory}/${file}`}
                    onClick={() => setSelectedFile(`${directory}/${file}`)}
                  >
                    <ListItemText primary={file} />
                  </ListItemButton>
                </ListItem>
              ))}
            </List>
          </Grid>

          {/* Main graph visualization */}
          <Grid item style={{ 
            width: hoveredTemplate ? `calc(100% - ${panelWidth + 200}px)` : 'calc(100% - 200px)', 
            height: '100%',
            position: 'relative'  // Add this for ReactFlow container
          }}>
            <div style={{ width: '100%', height: '100%' }}>  {/* Add wrapper div */}
              <ReactFlow
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                onEdgesChange={onEdgesChange}
                nodeTypes={nodeTypes}
                edgeTypes={edgeTypes}
                onInit={instance => {
                  reactFlowInstance.current = instance;
                }}
                fitView
              >
                <Background />
                <Controls />
              </ReactFlow>
            </div>
          </Grid>

          {/* Template code panel */}
          {hoveredTemplate && (
            <div style={{ 
              width: `${panelWidth}px`,
              height: '100%',
              position: 'relative',
              backgroundColor: 'white'
            }}>
              <div
                style={{
                  position: 'absolute',
                  left: -5,
                  top: 0,
                  bottom: 0,
                  width: 10,
                  cursor: 'col-resize',
                  zIndex: 1000,
                }}
                onMouseDown={(e) => {
                  e.preventDefault();
                  const startX = e.clientX;
                  const startWidth = panelWidth;

                  const handleMouseMove = (moveEvent) => {
                    moveEvent.preventDefault();
                    const deltaX = moveEvent.clientX - startX;
                    const newWidth = Math.max(
                      minWidth,
                      Math.min(
                        maxWidth,
                        startWidth - deltaX
                      )
                    );
                    setPanelWidth(newWidth);
                  };

                  const handleMouseUp = () => {
                    document.removeEventListener('mousemove', handleMouseMove);
                    document.removeEventListener('mouseup', handleMouseUp);
                  };

                  document.addEventListener('mousemove', handleMouseMove);
                  document.addEventListener('mouseup', handleMouseUp);
                }}
              />
              <Box sx={{ 
                height: '100%',
                borderLeft: 1,
                borderColor: 'divider',
              }}>
                <Box sx={{ 
                  height: '100%', 
                  display: 'flex', 
                  flexDirection: 'column' 
                }}>
                  <Box sx={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'center',
                    mb: 2,
                    px: 2,
                    pt: 2
                  }}>
                    <Typography variant="h6">Template Code</Typography>
                    <IconButton 
                      onClick={() => setHoveredTemplate(null)}
                      size="small"
                      aria-label="close"
                    >
                      <CloseIcon />
                    </IconButton>
                  </Box>
                  <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
                    <TemplateCode 
                      code={hoveredTemplate}
                      inputs={nodes.find(n => n.data.template === hoveredTemplate)?.data.inputs}
                      outputs={nodes.find(n => n.data.template === hoveredTemplate)?.data.outputs}
                      typeNames={nodes.find(n => n.data.template === hoveredTemplate)?.data.typeNames}
                    />
                  </Box>
                </Box>
              </Box>
            </div>
          )}
        </Box>
      </Box>
    </TemplateContext.Provider>
  );
}

// Add CSS for edge animation
const style = document.createElement('style');
style.textContent = `
  @keyframes flow {
    from {
      stroke-dashoffset: 24;
    }
    to {
      stroke-dashoffset: 0;
    }
  }
  
  .react-flow__edge-path {
    stroke-dasharray: 8;
  }
`;
document.head.appendChild(style);

export default View; 