import React, { useState, useEffect } from 'react';
import { Box, TextField, Typography, Alert, Stack, Grid, List, ListItem, ListItemButton, ListItemText, Button } from '@mui/material';
import ReactFlow, { Background, Controls, Handle, Position, useNodesState, useEdgesState } from 'reactflow';
import 'reactflow/dist/style.css';
import axios from 'axios';

const DEFAULT_PORT = process.env.REACT_APP_DEFAULT_LOG_PORT || '3000';
const DEFAULT_SCHEMA_URL = `http://localhost:${DEFAULT_PORT}/schema.json`;

// Pastel colors for types (reused from Schema.js)
const PASTEL_COLORS = [
  '#FFB3BA', '#BAFFC9', '#BAE1FF', '#FFFFBA', '#E8BAFF', 
  '#FFD9BA', '#B3FFE5', '#FFB3E6', '#B3ECFF', '#E5FFB3',
];

const getTypeColor = (idx) => {
  return PASTEL_COLORS[idx % PASTEL_COLORS.length];
};

// Custom node for endpoints (similar to Fuzz.js)
const EndpointNode = ({ data }) => {
  const nodeHeight = 80;
  const circleSize = 16;
  const circleOffset = circleSize / 2;

  return (
    <div style={{ position: 'relative' }}>
      <div 
        style={{ 
          padding: '15px',
          paddingTop: '20px',
          paddingBottom: '20px',
          borderRadius: '8px',
          background: 'white',
          border: '1px solid #ddd',
          minWidth: '150px',
          height: `${nodeHeight}px`,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          opacity: data.isSelected ? 1 : data.isHovered ? 0.9 : 0.5,
          transition: 'opacity 0.2s'
        }}
      >
        <Typography variant="subtitle2" align="center">{data.name}</Typography>
      </div>
      
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
          <Handle
            key={`in-${idx}`}
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
              left: 'auto'
            }}
          />
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
          <Handle
            key={`out-${idx}`}
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
              left: 'auto'
            }}
          />
        ))}
      </div>
    </div>
  );
};

// Custom node for type
const TypeNode = ({ data }) => {
  const circleSize = 16;
  return (
    <div style={{
      padding: '10px 20px',
      borderRadius: '4px',
      background: getTypeColor(data.typeIdx),
      border: '1px solid #666',
      textAlign: 'center',
      position: 'relative'
    }}>
      <Typography variant="subtitle1">{data.name}</Typography>
      <Handle
        type="source"
        position={Position.Bottom}
        style={{
          width: circleSize,
          height: circleSize,
          background: getTypeColor(data.typeIdx),
          border: '1px solid #666',
          borderRadius: '50%'
        }}
      />
      <Handle
        type="target"
        position={Position.Top}
        style={{
          width: circleSize,
          height: circleSize,
          background: getTypeColor(data.typeIdx),
          border: '1px solid #666',
          borderRadius: '50%'
        }}
      />
    </div>
  );
};

// Custom edge component
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

  // Don't render anything if opacity is 0
  if (style.opacity === 0) return null;

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
          opacity: style.opacity
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

const nodeTypes = {
  endpoint: EndpointNode,
  type: TypeNode,
};

const edgeTypes = {
  custom: CustomEdge,
};

function Sampler() {
  const [schemaUrl, setSchemaUrl] = useState(() => {
    const saved = localStorage.getItem('lastSchemaUrl');
    return saved || DEFAULT_SCHEMA_URL;
  });
  const [schema, setSchema] = useState(null);
  const [cacheData, setCacheData] = useState(null);
  const [error, setError] = useState(null);
  const [cacheError, setCacheError] = useState(null);
  const [debouncedUrl, setDebouncedUrl] = useState(schemaUrl);
  const [selectedType, setSelectedType] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedDepth, setSelectedDepth] = useState(null);
  const [selectedDirection, setSelectedDirection] = useState(null);
  const [selectedEndpoint, setSelectedEndpoint] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [hoveredNode, setHoveredNode] = useState(null);

  // Reset selection when type changes
  useEffect(() => {
    setSelectedDepth(null);
    setSelectedDirection(null);
    setSelectedNode(null);
  }, [selectedType]);

  // Update localStorage when URL changes
  useEffect(() => {
    localStorage.setItem('lastSchemaUrl', schemaUrl);
  }, [schemaUrl]);

  // Debounce the URL changes
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedUrl(schemaUrl);
    }, 500);

    return () => clearTimeout(timer);
  }, [schemaUrl]);

  const fetchData = async (url) => {
    try {
      // Basic URL validation
      if (!url || !url.startsWith('http')) {
        setError('Please enter a valid URL');
        return;
      }

      // Fetch schema
      const schemaResponse = await axios.get(url);
      setSchema(schemaResponse.data);
      setError(null);

      // Try to fetch cache file
      try {
        const cacheUrl = `${url}.cache`;
        const cacheResponse = await axios.get(cacheUrl);
        const data = cacheResponse.data;
        
        // Transform the nodes array into a map
        const nodes = {};
        if (Array.isArray(data.nodes)) {
          data.nodes.forEach(entry => {
            if (Array.isArray(entry) && entry.length === 2) {
              const [keyArray, value] = entry;
              const key = keyArray.join(',');
              nodes[key] = value;
            }
          });
        }

        const transformedCache = {
          nodes,
          enabled_endpoints: data.enabled_endpoints || []
        };
        setCacheData(transformedCache);
        setCacheError(null);
      } catch (cacheErr) {
        console.error('Error fetching cache:', cacheErr);
        setCacheError('Cache file not found');
        setCacheData(null);
      }
    } catch (error) {
      console.error('Error fetching schema:', error);
      setError('Error loading schema');
      setSchema(null);
    }
  };

  useEffect(() => {
    fetchData(debouncedUrl);
  }, [debouncedUrl]);

  // Update graph when selection changes
  useEffect(() => {
    if (!selectedType || !selectedNode) {
      setNodes([]);
      setEdges([]);
      return;
    }

    const newNodes = [];
    const newEdges = [];

    // Add center type node
    newNodes.push({
      id: 'type',
      type: 'type',
      position: { x: 300, y: 200 },
      data: { 
        name: selectedType.name,
        typeIdx: selectedType.idx
      }
    });

    // Add endpoint nodes
    selectedNode.children.forEach((child, idx) => {
      const endpoint = schema.endpoints[child.idx];
      const x = 150 + (idx * 250);
      const y = selectedDirection ? 400 : 0;

      newNodes.push({
        id: `endpoint-${idx}`,
        type: 'endpoint',
        draggable: false,
        position: { x, y },
        data: {
          name: endpoint.name,
          inputs: endpoint.inputs,
          outputs: endpoint.outputs,
          isHovered: hoveredNode === `endpoint-${idx}`,
          isSelected: selectedEndpoint === idx,
          child: child
        }
      });

      // Add edge
      const edgeStyle = {
        stroke: selectedEndpoint === null ? 
          (hoveredNode === `endpoint-${idx}` ? getTypeColor(selectedType.idx) : '#e0e0e0') : 
          (selectedEndpoint === idx ? getTypeColor(selectedType.idx) : 'transparent'),
        strokeWidth: 2,
        transition: 'all 0.2s',
        opacity: selectedEndpoint === null || selectedEndpoint === idx ? 1 : 0,
        pointerEvents: selectedEndpoint === null || selectedEndpoint === idx ? 'auto' : 'none'
      };

      if (selectedDirection) { // Forward
        newEdges.push({
          id: `edge-${idx}`,
          source: 'type',
          target: `endpoint-${idx}`,
          targetHandle: `in-${child.conn_idx}`,
          style: edgeStyle,
          type: 'custom'
        });
      } else { // Backward
        newEdges.push({
          id: `edge-${idx}`,
          source: `endpoint-${idx}`,
          sourceHandle: `out-${child.conn_idx}`,
          target: 'type',
          style: edgeStyle,
          type: 'custom'
        });
      }
    });

    setNodes(newNodes);
    setEdges(newEdges);
  }, [selectedType, selectedNode, selectedDirection, hoveredNode, selectedEndpoint, schema]);

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* Toolbar */}
      <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider', display: 'flex', alignItems: 'center', gap: 2 }}>
        <TextField
          label="Schema URL"
          variant="outlined"
          value={schemaUrl}
          onChange={(e) => setSchemaUrl(e.target.value)}
          size="small"
          sx={{ width: '600px' }}
          error={!!error}
          helperText={error}
        />
        <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
          {schema && <Alert severity="success" sx={{ py: 0 }}>Schema loaded</Alert>}
          {cacheData && <Alert severity="success" sx={{ py: 0 }}>Cache loaded</Alert>}
          {cacheError && <Alert severity="info" sx={{ py: 0 }}>No cache found</Alert>}
        </Stack>
      </Box>
      
      {/* Main content area */}
      <Box sx={{ flexGrow: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Sidebar */}
        <Grid item xs={3} sx={{ borderRight: 1, borderColor: 'divider', height: '100%', overflow: 'auto' }}>
          <Typography variant="h6" sx={{ p: 2 }}>Types</Typography>
          <List>
            {schema?.types.map((type) => (
              <ListItem key={type.idx} disablePadding>
                <ListItemButton 
                  selected={selectedType?.idx === type.idx}
                  onClick={() => setSelectedType(type)}
                >
                  <Box sx={{ display: 'flex', alignItems: 'center', width: '100%' }}>
                    <Box 
                      sx={{ 
                        width: 16, 
                        height: 16, 
                        borderRadius: '50%', 
                        backgroundColor: getTypeColor(type.idx),
                        border: '1px solid #666',
                        mr: 1
                      }} 
                    />
                    <ListItemText primary={type.name} secondary={`idx: ${type.idx}`} />
                  </Box>
                </ListItemButton>
              </ListItem>
            ))}
          </List>
        </Grid>

        {/* Main content */}
        <Box sx={{ flexGrow: 1, overflow: 'auto' }}>
          {selectedType && cacheData && (
            <Box>
              {/* Top control bar */}
              <Box sx={{ 
                borderBottom: 1,
                borderColor: 'divider',
                bgcolor: 'background.paper',
                display: 'flex',
                alignItems: 'center',
                p: 1,
                gap: 3
              }}>
                {[1, 2, 3].map(depth => (
                  <Box key={depth} sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                    <Typography variant="body2" sx={{ color: 'text.secondary', minWidth: '60px' }}>
                      Depth {depth}:
                    </Typography>
                    <Box sx={{ display: 'flex', gap: 0.5 }}>
                      {[true, false].map(isForward => {
                        const key = [selectedType.idx, depth, isForward].join(',');
                        const node = cacheData.nodes[key];
                        const isEnabled = node?.enabled ?? false;
                        const isSelected = selectedDepth === depth && selectedDirection === isForward;
                        
                        return (
                          <Button
                            key={isForward ? 'forward' : 'backward'}
                            variant={isSelected ? "contained" : "outlined"}
                            disabled={!isEnabled}
                            size="small"
                            onClick={() => {
                              if (isSelected) {
                                // Deselect if clicking the same button
                                setSelectedDepth(null);
                                setSelectedDirection(null);
                                setSelectedNode(null);
                              } else {
                                setSelectedDepth(depth);
                                setSelectedDirection(isForward);
                                setSelectedNode(node);
                              }
                            }}
                            sx={{ 
                              minWidth: '80px',
                              textTransform: 'none',
                              ...(isSelected ? {
                                bgcolor: isEnabled ? 'primary.main' : 'grey.400',
                                color: 'white',
                                '&:hover': {
                                  bgcolor: isEnabled ? 'primary.dark' : 'grey.500',
                                }
                              } : {
                                borderColor: isEnabled ? 'primary.main' : 'grey.300',
                                color: isEnabled ? 'primary.main' : 'grey.500'
                              })
                            }}
                          >
                            {isForward ? 'Forward' : 'Backward'}
                          </Button>
                        );
                      })}
                    </Box>
                  </Box>
                ))}
              </Box>

              {/* Graph view area */}
              <Box sx={{ 
                height: 'calc(100vh - 200px)', 
                bgcolor: 'grey.100'
              }}>
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  nodeTypes={nodeTypes}
                  edgeTypes={edgeTypes}
                  defaultEdgeOptions={{
                    type: 'custom',
                    animated: true
                  }}
                  onNodeMouseEnter={(_, node) => setHoveredNode(node.id)}
                  onNodeMouseLeave={() => setHoveredNode(null)}
                  onNodeClick={(_, node) => {
                    if (node.type === 'endpoint') {
                      const endpointIdx = parseInt(node.id.split('-')[1]);
                      setSelectedEndpoint(selectedEndpoint === endpointIdx ? null : endpointIdx);
                    }
                  }}
                  nodesConnectable={false}
                  nodesDraggable={false}
                  fitView
                >
                  <Background />
                  <Controls />
                </ReactFlow>
              </Box>

              {/* Selected node information */}
              {selectedNode && (
                <Box sx={{ 
                  position: 'absolute',
                  bottom: 20,
                  right: 20,
                  width: '400px',
                  bgcolor: 'background.paper',
                  boxShadow: 3,
                  borderRadius: 1,
                  p: 2
                }}>
                  <Typography variant="subtitle1" gutterBottom>
                    Selected TypeNode Information
                  </Typography>
                  <Typography variant="body2" color={selectedNode.enabled ? 'success.main' : 'error.main'} gutterBottom>
                    Status: {selectedNode.enabled ? 'Enabled' : 'Disabled'}
                  </Typography>
                  <Typography variant="subtitle2" gutterBottom>
                    Children ({selectedNode.children.length}):
                  </Typography>
                  {selectedNode.children.length > 0 ? (
                    <Box component="ul" sx={{ mt: 1, pl: 2, mb: 0 }}>
                      {selectedNode.children.map((child, i) => (
                        <Typography component="li" key={i}>
                          Endpoint {child.idx} (Connection {child.conn_idx})
                          {child.enabled ? ' - Enabled' : ' - Disabled'}
                        </Typography>
                      ))}
                    </Box>
                  ) : (
                    <Typography variant="body2" color="text.secondary">No children</Typography>
                  )}
                </Box>
              )}
            </Box>
          )}
        </Box>
      </Box>
    </Box>
  );
}

export default Sampler; 