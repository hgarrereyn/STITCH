import React, { useState, useEffect, useCallback, createContext, useContext } from 'react';
import { Box, TextField, Grid, List, ListItem, ListItemButton, ListItemText, Typography, Tooltip, Paper, IconButton, Collapse } from '@mui/material';
import ReactFlow, { Background, Controls, useNodesState, useEdgesState } from 'reactflow';
import 'reactflow/dist/style.css';
import axios from 'axios';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { coy } from 'react-syntax-highlighter/dist/esm/styles/prism';
import CloseIcon from '@mui/icons-material/Close';
import { Resizable } from 'react-resizable';
import 'react-resizable/css/styles.css';
import ExpandLess from '@mui/icons-material/ExpandLess';
import ExpandMore from '@mui/icons-material/ExpandMore';

const DEFAULT_PORT = process.env.REACT_APP_DEFAULT_LOG_PORT || '3000';
const DEFAULT_SCHEMA_URL = `http://localhost:${DEFAULT_PORT}/schema.json`;

// Pastel colors for types
const PASTEL_COLORS = [
  '#FFB3BA', // pink
  '#BAFFC9', // green
  '#BAE1FF', // blue
  '#FFFFBA', // yellow
  '#E8BAFF', // purple
  '#FFD9BA', // orange
  '#B3FFE5', // mint
  '#FFB3E6', // magenta
  '#B3ECFF', // cyan
  '#E5FFB3', // lime
];

// Get a consistent color for a type index
const getTypeColor = (idx) => {
  return PASTEL_COLORS[idx % PASTEL_COLORS.length];
};

// Update TemplateCode component to include type information
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
        minHeight: 0  // Important for flex child to shrink properly
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
          marginBottom: '12px'  // Move down by 12px
        }}>
          <TypeList types={outputs} prefix="o" />
        </Box>
      )}
    </Box>
  );
};

// Create context for template state
const TemplateContext = createContext();

// Custom node for endpoints
const EndpointNode = ({ data }) => {
  const nodeHeight = 80;
  const circleSize = 16;
  const circleOffset = circleSize / 2;

  // Use the context hook to get setHoveredTemplate
  const { setHoveredTemplate, hoveredTemplate } = useContext(TemplateContext);

  const handleClick = () => {
    // If clicking the same endpoint, do nothing
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
      
      {/* Input type circles */}
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
            <div style={{ 
              width: circleSize,
              height: circleSize,
              borderRadius: '50%',
              background: getTypeColor(typeIdx),
              border: '1px solid #666',
              cursor: 'pointer'
            }} />
          </Tooltip>
        ))}
      </div>

      {/* Output type circles */}
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
            <div style={{ 
              width: circleSize,
              height: circleSize,
              borderRadius: '50%',
              background: getTypeColor(typeIdx),
              border: '1px solid #666',
              cursor: 'pointer'
            }} />
          </Tooltip>
        ))}
      </div>
    </div>
  );
};

const nodeTypes = {
  endpoint: EndpointNode,
};

function Schema() {
  const [schemaUrl, setSchemaUrl] = useState(() => {
    const saved = localStorage.getItem('lastSchemaUrl');
    return saved || DEFAULT_SCHEMA_URL;
  });
  const [schema, setSchema] = useState({ types: [], endpoints: [] });
  const [selectedType, setSelectedType] = useState(null);
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [error, setError] = useState(null);
  const [debouncedUrl, setDebouncedUrl] = useState(schemaUrl);
  const [hoveredTemplate, setHoveredTemplate] = useState(null);
  const [panelWidth, setPanelWidth] = useState(400); // Initial width in pixels
  const [isDragging, setIsDragging] = useState(false);
  const [typesOpen, setTypesOpen] = useState(true);
  const [endpointsOpen, setEndpointsOpen] = useState(true);

  // Calculate min and max widths in pixels
  const minWidth = Math.max(200, window.innerWidth * 0.15);  // At least 200px or 15% of window
  const maxWidth = window.innerWidth * 0.6;  // Max 60% of window

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

  const fetchSchema = async (url) => {
    try {
      // Basic URL validation
      if (!url || !url.startsWith('http')) {
        setError('Please enter a valid URL');
        return;
      }

      // Add cache-busting timestamp
      const cacheBustUrl = `${url}${url.includes('?') ? '&' : '?'}_t=${Date.now()}`;
      const response = await axios.get(cacheBustUrl);
      
      // Validate schema structure
      if (!response.data || !Array.isArray(response.data.types) || !Array.isArray(response.data.endpoints)) {
        setError('Invalid schema format');
        return;
      }

      // Debug log to see what we're getting
      console.log('Received schema:', response.data);
      console.log('Sample endpoint:', response.data.endpoints[0]);

      setSchema(response.data);
      setError(null);
    } catch (error) {
      console.error('Error fetching schema:', error);
      setError('Error loading schema');
    }
  };

  useEffect(() => {
    fetchSchema(debouncedUrl);
  }, [debouncedUrl]);

  useEffect(() => {
    if (!selectedType) {
      setNodes([]);
      setEdges([]);
      return;
    }

    const newNodes = [];
    const newEdges = [];
    
    // Create a mapping of type indices to names for tooltips
    const typeNames = Object.fromEntries(
      schema.types.map(type => [type.idx, type.name])
    );
    
    // Add the selected type node in the center
    newNodes.push({
      id: `type-${selectedType.idx}`,
      type: 'default',
      position: { x: 300, y: 200 },
      data: { 
        label: selectedType.name,
        color: getTypeColor(selectedType.idx)
      },
      style: {
        background: getTypeColor(selectedType.idx),
        border: '1px solid #666',
        borderRadius: '4px',
        padding: '10px'
      }
    });

    // Add endpoints that take this type as input (below)
    const inputEndpoints = schema.endpoints.filter(ep => 
      ep.inputs.includes(selectedType.idx)
    );

    inputEndpoints.forEach((ep, idx) => {
      const x = 150 + (idx * 200);
      const y = 400;
      
      newNodes.push({
        id: `endpoint-in-${ep.idx}`,
        type: 'endpoint',
        position: { x, y },
        data: { 
          name: ep.name,
          inputs: ep.inputs,
          outputs: ep.outputs,
          typeNames,
          template: ep.template
        }
      });

      newEdges.push({
        id: `edge-in-${ep.idx}`,
        source: `type-${selectedType.idx}`,
        target: `endpoint-in-${ep.idx}`,
        type: 'smoothstep'
      });
    });

    // Add endpoints that produce this type as output (above)
    const outputEndpoints = schema.endpoints.filter(ep => 
      ep.outputs.includes(selectedType.idx)
    );

    outputEndpoints.forEach((ep, idx) => {
      const x = 150 + (idx * 200);
      const y = 0;
      
      newNodes.push({
        id: `endpoint-out-${ep.idx}`,
        type: 'endpoint',
        position: { x, y },
        data: { 
          name: ep.name,
          inputs: ep.inputs,
          outputs: ep.outputs,
          typeNames,
          template: ep.template
        }
      });

      newEdges.push({
        id: `edge-out-${ep.idx}`,
        source: `endpoint-out-${ep.idx}`,
        target: `type-${selectedType.idx}`,
        type: 'smoothstep'
      });
    });

    setNodes(newNodes);
    setEdges(newEdges);
  }, [selectedType, schema]);

  return (
    <TemplateContext.Provider value={{ hoveredTemplate, setHoveredTemplate }}>
      <Box sx={{ flexGrow: 1, height: 'calc(100vh - 48px)', overflow: 'hidden' }}>
        <Box sx={{ p: 2, borderBottom: 1, borderColor: 'divider' }}>
          <TextField
            fullWidth
            label="Schema File URL"
            value={schemaUrl}
            onChange={(e) => setSchemaUrl(e.target.value)}
            variant="outlined"
            size="small"
            error={!!error}
            helperText={error || `Default port is ${DEFAULT_PORT}. Change the port by setting REACT_APP_DEFAULT_LOG_PORT environment variable.`}
          />
        </Box>
        <Grid container sx={{ height: 'calc(100% - 80px)' }}>
          {/* Sidebar with types and endpoints */}
          <Grid item style={{ width: '200px', borderRight: '1px solid rgba(0, 0, 0, 0.12)', height: '100%', overflow: 'auto' }}>
            {/* Types Section */}
            <ListItemButton onClick={() => setTypesOpen(!typesOpen)}>
              <ListItemText primary="Types" />
              {typesOpen ? <ExpandLess /> : <ExpandMore />}
            </ListItemButton>
            <Collapse in={typesOpen} timeout="auto" unmountOnExit>
              <List>
                {schema.types.map((type) => (
                  <ListItem key={type.idx} disablePadding>
                    <ListItemButton 
                      selected={selectedType?.idx === type.idx}
                      onClick={() => setSelectedType(type)}
                      sx={{ pl: 4 }}
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
            </Collapse>

            {/* Endpoints Section */}
            <ListItemButton onClick={() => setEndpointsOpen(!endpointsOpen)}>
              <ListItemText primary="Endpoints" />
              {endpointsOpen ? <ExpandLess /> : <ExpandMore />}
            </ListItemButton>
            <Collapse in={endpointsOpen} timeout="auto" unmountOnExit>
              <List>
                {schema.endpoints.map((endpoint) => (
                  <ListItem key={endpoint.idx} disablePadding>
                    <ListItemButton 
                      onClick={() => {
                        setHoveredTemplate(endpoint.template);
                        setSelectedType(null);  // Clear selected type when clicking endpoint
                      }}
                      selected={hoveredTemplate === endpoint.template}
                      sx={{ pl: 4 }}
                    >
                      <ListItemText 
                        primary={endpoint.name} 
                        secondary={`idx: ${endpoint.idx}`}
                      />
                    </ListItemButton>
                  </ListItem>
                ))}
              </List>
            </Collapse>
          </Grid>
          
          {/* Main content area */}
          <Grid item style={{ 
            width: 'calc(100% - 200px)', 
            height: '100%'
          }}>
            {selectedType ? (
              // Show graph visualization when a type is selected
              <Box sx={{ display: 'flex', width: '100%', height: '100%' }}>
                <Box sx={{ 
                  width: hoveredTemplate ? `calc(100% - ${panelWidth}px)` : '100%', 
                  height: '100%' 
                }}>
                  <ReactFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    nodeTypes={nodeTypes}
                    fitView
                  >
                    <Background />
                    <Controls />
                  </ReactFlow>
                </Box>

                {/* Template code panel when viewing graph */}
                {hoveredTemplate && (
                  <div style={{ 
                    width: `${panelWidth}px`,
                    height: '100%',
                    position: 'relative',
                    backgroundColor: 'white',
                    borderLeft: '1px solid rgba(0, 0, 0, 0.12)'
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
                  </div>
                )}
              </Box>
            ) : (
              // Show full-width template code when no type is selected
              hoveredTemplate && (
                <Box sx={{ 
                  height: '100%',
                  display: 'flex', 
                  flexDirection: 'column',
                  backgroundColor: 'white'
                }}>
                  <Box sx={{ 
                    display: 'flex', 
                    justifyContent: 'space-between', 
                    alignItems: 'center',
                    mb: 2,
                    px: 2,
                    pt: 2,
                    borderBottom: '1px solid rgba(0, 0, 0, 0.12)'
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
                  <Box sx={{ flexGrow: 1, overflow: 'auto', px: 2 }}>
                    <TemplateCode 
                      code={hoveredTemplate}
                      inputs={schema.endpoints.find(e => e.template === hoveredTemplate)?.inputs}
                      outputs={schema.endpoints.find(e => e.template === hoveredTemplate)?.outputs}
                      typeNames={Object.fromEntries(schema.types.map(type => [type.idx, type.name]))}
                    />
                  </Box>
                </Box>
              )
            )}
          </Grid>
        </Grid>
      </Box>
    </TemplateContext.Provider>
  );
}

export default Schema; 