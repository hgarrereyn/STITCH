document.addEventListener('DOMContentLoaded', () => {
    const dropArea = document.getElementById('drop-area');
    const fileInput = document.getElementById('file-input');
    const graphContainer = document.getElementById('graph-container');
    const graphView = document.getElementById('graph-view');
    const nodeInfo = document.getElementById('node-info');
    const nodeDetails = document.getElementById('node-details');
    const zoomInBtn = document.getElementById('zoom-in');
    const zoomOutBtn = document.getElementById('zoom-out');
    const resetViewBtn = document.getElementById('reset-view');
    const downloadSvgBtn = document.getElementById('download-svg');
    const backButton = document.getElementById('back-button');
    const closeDetailsBtn = document.getElementById('close-details');

    let currentGraph = null;
    let svg = null;
    let svgGroup = null;
    let zoom = null;
    let renderer = null;
    let selectedNode = null;
    let g = null; // Store the graph
    let pspec = null; // Store the PSpec information

    // Prevent default drag behaviors
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, preventDefaults, false);
        document.body.addEventListener(eventName, preventDefaults, false);
    });

    // Highlight drop area when item is dragged over it
    ['dragenter', 'dragover'].forEach(eventName => {
        dropArea.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropArea.addEventListener(eventName, unhighlight, false);
    });

    // Handle dropped files
    dropArea.addEventListener('drop', handleDrop, false);

    // Handle file input change
    fileInput.addEventListener('change', handleFileInput, false);

    // Zoom controls
    zoomInBtn.addEventListener('click', () => {
        if (zoom) {
            zoom.scaleBy(svg.transition().duration(200), 1.2);
        }
    });

    zoomOutBtn.addEventListener('click', () => {
        if (zoom) {
            zoom.scaleBy(svg.transition().duration(200), 0.8);
        }
    });

    resetViewBtn.addEventListener('click', resetZoom);

    downloadSvgBtn.addEventListener('click', downloadSVG);
    
    // Back button handler
    backButton.addEventListener('click', () => {
        graphContainer.classList.add('hidden');
        dropArea.classList.remove('hidden');
        graphView.innerHTML = '';
        document.title = "SGraph Viewer";
        pspec = null;
    });
    
    // Close details button handler
    closeDetailsBtn.addEventListener('click', () => {
        nodeInfo.classList.add('hidden');
        if (selectedNode) {
            svgGroup.select(`#${selectedNode}`).classed("selected", false);
            selectedNode = null;
        }
    });

    // Handle window resize
    window.addEventListener('resize', debounce(() => {
        if (svg && zoom && g) {
            resetZoom();
        }
    }, 250));

    function resetZoom() {
        if (zoom && svg && svgGroup && g) {
            const svgEl = svg.node();
            const svgWidth = svgEl.getBoundingClientRect().width;
            const svgHeight = svgEl.getBoundingClientRect().height;
            
            const graphWidth = g.graph().width + 80;
            const graphHeight = g.graph().height + 40;
            
            const scale = Math.min(svgWidth / graphWidth, svgHeight / graphHeight, 0.9);
            const translateX = (svgWidth - graphWidth * scale) / 2;
            const translateY = (svgHeight - graphHeight * scale) / 2;
            
            svg.transition()
               .duration(500)
               .call(zoom.transform, d3.zoomIdentity.translate(translateX, translateY).scale(scale));
        }
    }

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    function highlight() {
        dropArea.classList.add('highlight');
    }

    function unhighlight() {
        dropArea.classList.remove('highlight');
    }

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    }

    function handleFileInput() {
        const files = fileInput.files;
        handleFiles(files);
    }

    function handleFiles(files) {
        if (files.length > 0) {
            const file = files[0];
            if (file.type === 'application/json' || file.name.endsWith('.json')) {
                readFile(file);
            } else {
                alert('Please select a JSON file.');
            }
        }
    }

    function readFile(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const data = JSON.parse(e.target.result);
                if (isValidSGraph(data)) {
                    // Set the document title to include the file name
                    document.title = `SGraph Viewer - ${file.name}`;
                    
                    // Extract PSpec if available
                    if (data.pspec) {
                        pspec = data.pspec;
                    } else {
                        pspec = null;
                    }
                    
                    // Hide drop area and show graph
                    dropArea.classList.add('hidden');
                    graphContainer.classList.remove('hidden');
                    
                    // Render the graph
                    renderGraph(data);
                } else {
                    alert('The file does not appear to be a valid SGraph JSON.');
                }
            } catch (error) {
                console.error('Error parsing JSON:', error);
                alert('Error parsing JSON file. Please make sure it is a valid JSON file.');
            }
        };
        reader.readAsText(file);
    }

    function isValidSGraph(data) {
        // Basic validation of SGraph format
        return data && 
               Array.isArray(data.nodes);
    }

    // Helper function to get function name from PSpec
    function getFunctionName(funcIdx) {
        if (pspec && pspec.endpoints && funcIdx < pspec.endpoints.length) {
            return pspec.endpoints[funcIdx].name;
        }
        return `Function ${funcIdx}`;
    }

    // Helper function to get object name from PSpec
    function getObjectName(objectIdx, instanceIdx = 0) {
        if (pspec && pspec.objects && objectIdx < pspec.objects.length) {
            return `${pspec.objects[objectIdx].name}_${instanceIdx}`;
        }
        return `Object ${objectIdx}`;
    }

    // Helper function to get input/output variable name
    function getVariableName(funcIdx, isInput, idx) {
        if (pspec && pspec.endpoints && funcIdx < pspec.endpoints.length) {
            const endpoint = pspec.endpoints[funcIdx];
            if (isInput && endpoint.input_vars && idx < endpoint.input_vars.length) {
                return endpoint.input_vars[idx];
            } else if (!isInput && endpoint.output_vars && idx < endpoint.output_vars.length) {
                return endpoint.output_vars[idx];
            }
        }
        return isInput ? `Input ${idx}` : `Output ${idx}`;
    }

    function renderGraph(sgraph) {
        currentGraph = sgraph;
        graphView.innerHTML = '';
        
        // Hide node info if visible
        nodeInfo.classList.add('hidden');
        selectedNode = null;

        // Create a new directed graph
        g = new dagreD3.graphlib.Graph({ multigraph: true })
            .setGraph({
                rankdir: 'TB', // Top to Bottom layout
                marginx: 20,
                marginy: 20,
                nodeSep: 70,
                edgeSep: 30,
                ranksep: 150, // Increased to accommodate ports
                nodesep: 70
            })
            .setDefaultEdgeLabel(() => ({}));

        // Store port positions for edge connections
        const portPositions = {};

        // Add nodes to the graph with labels
        sgraph.nodes.forEach((node, index) => {
            const inputCount = node.inputs ? node.inputs.length : 0;
            const outputCount = node.outputs ? node.outputs.length : 0;
            const funcName = getFunctionName(node.func_idx);
            const funcType = getFunctionType(node.func_idx);
            
            // Create input port HTML
            let inputPortsHtml = '';
            if (inputCount > 0 || true) {
                inputPortsHtml = '<div class="port-container input-ports">';
                for (let i = 0; i < inputCount; i++) {
                    const varName = getVariableName(node.func_idx, true, i);
                    inputPortsHtml += `<div class="port input-port" data-port-id="in-${i}">
                        <div class="port-label">${varName}</div>
                    </div>`;
                }
                inputPortsHtml += '</div>';
            }
            
            // Create output port HTML
            let outputPortsHtml = '';
            if (outputCount > 0 || true) {
                outputPortsHtml = '<div class="port-container output-ports">';
                for (let i = 0; i < outputCount; i++) {
                    const varName = getVariableName(node.func_idx, false, i);
                    outputPortsHtml += `<div class="port output-port" data-port-id="out-${i}">
                        <div class="port-label">${varName}</div>
                    </div>`;
                }
                outputPortsHtml += '</div>';
            }
            
            // Create the node label with input/output ports
            const label = `<div class="node-content">
                ${inputPortsHtml}
                <div class="node-label">(${index}) ${funcName}</div>
                ${outputPortsHtml}
            </div>`;
            
            // Store node in graph
            g.setNode(index.toString(), {
                label: label,
                labelType: 'html',
                rx: 5,
                ry: 5,
                // padding: 10,
                width: Math.max(200, Math.max(inputCount, outputCount) * 50),
                height: 50,
                data: { node, index },
                class: funcType
            });
            
            // Initialize port position tracking
            portPositions[index] = {
                inputs: {},
                outputs: {}
            };
        });

        // Add edges to the graph - we'll connect them properly after rendering
        sgraph.nodes.forEach((node, nodeIndex) => {
            if (!node.outputs) return;
            
            node.outputs.forEach((edge, outputIndex) => {
                if (edge.other_idx === undefined || edge.other_idx === null || 
                    edge.other_idx === Number.MAX_SAFE_INTEGER) return;
                
                // Get a nice object name if available
                const objectName = getObjectName(edge.object_idx);
                const objectDetails = getObjectDetails(edge.object_idx);
                
                // Add edge - we'll set proper points after rendering
                g.setEdge(
                    nodeIndex.toString(),
                    edge.other_idx.toString(),
                    {
                        label: objectName,
                        labelpos: 'c',
                        labeloffset: 5,
                        arrowheadClass: 'arrowhead',
                        id: `edge-${nodeIndex}-${outputIndex}`,
                        tooltip: objectDetails,
                        sourcePort: `out-${outputIndex}`,
                        targetPort: `in-${edge.other_conn_idx}`
                    },
                    `edge-${nodeIndex}-${outputIndex}`
                );
            });
        });

        // Create SVG and group elements
        svg = d3.select(graphView).append("svg");
        svgGroup = svg.append("g");

        // Set up zoom support
        zoom = d3.zoom()
            .on("zoom", (event) => {
                svgGroup.attr("transform", event.transform);
            });
        svg.call(zoom);

        // Create the renderer
        renderer = new dagreD3.render();

        // Run the renderer
        renderer(svgGroup, g);

        // Calculate port positions
        svgGroup.selectAll(".node").each(function(nodeId) {
            const nodeEl = this;
            const nodeWidth = g.node(nodeId).width;
            const nodeHeight = g.node(nodeId).height;
            
            // Find input ports
            d3.select(nodeEl).selectAll(".input-port").each(function() {
                const portEl = this;
                const portId = d3.select(portEl).attr("data-port-id");
                const portBBox = portEl.getBoundingClientRect();
                const nodeBBox = nodeEl.getBoundingClientRect();
                
                // Calculate relative position within the node
                const portX = (portBBox.left + portBBox.width/2) - nodeBBox.left;
                
                // Store port position (top center of port)
                portPositions[nodeId].inputs[portId] = {
                    x: portX,
                    y: 0 // Top of node
                };
            });
            
            // Find output ports
            d3.select(nodeEl).selectAll(".output-port").each(function() {
                const portEl = this;
                const portId = d3.select(portEl).attr("data-port-id");
                const portBBox = portEl.getBoundingClientRect();
                const nodeBBox = nodeEl.getBoundingClientRect();
                
                // Calculate relative position within the node
                const portX = (portBBox.left + portBBox.width/2) - nodeBBox.left;
                
                // Store port position (bottom center of port)
                portPositions[nodeId].outputs[portId] = {
                    x: portX,
                    y: nodeHeight // Bottom of node
                };
            });
        });

        // Adjust the edge paths to connect to ports
        svgGroup.selectAll("g.edgePath").each(function(edgeId) {
            const edge = g.edge(edgeId);
            const sourceId = edgeId.v;
            const targetId = edgeId.w;
            
            if (!edge.sourcePort || !edge.targetPort) return;
            
            // Get the port positions
            const sourcePort = portPositions[sourceId].outputs[edge.sourcePort];
            const targetPort = portPositions[targetId].inputs[edge.targetPort];
            
            if (!sourcePort || !targetPort) return;
            
            // Get node positions
            const sourceNode = g.node(sourceId);
            const targetNode = g.node(targetId);
            
            // Calculate absolute source and target points
            const sourcePoint = {
                x: sourceNode.x - (sourceNode.width / 2) + sourcePort.x,
                y: sourceNode.y - (sourceNode.height / 2) + sourcePort.y
            };
            
            const targetPoint = {
                x: targetNode.x - (targetNode.width / 2) + targetPort.x,
                y: targetNode.y - (targetNode.height / 2) + targetPort.y
            };
            
            // Update the edge points (this is a simplification as dagre-d3 doesn't easily allow this)
            // We'll use d3.select to update the path directly
            const path = d3.select(this).select("path");
            
            // Create a curved path
            const midY = (sourcePoint.y + targetPoint.y) / 2;
            const pathD = `M ${sourcePoint.x} ${sourcePoint.y} C ${sourcePoint.x} ${midY}, ${targetPoint.x} ${midY}, ${targetPoint.x} ${targetPoint.y}`;
            
            path.attr("d", pathD);
        });

        // Set up initial zoom
        resetZoom();

        // Setup node click event
        svgGroup.selectAll("g.node")
            .on("click", function(event) {
                // Only trigger if clicking on the node itself, not a port
                if (event.target.classList && (event.target.classList.contains('port') || 
                    event.target.classList.contains('port-label'))) {
                    return;
                }
                
                // Get node data
                const nodeId = this.id;
                const nodeData = g.node(nodeId).data;
                
                // Deselect previous node
                if (selectedNode) {
                    svgGroup.select(`#${selectedNode}`)
                        .classed("selected", false);
                }
                
                // Select this node
                d3.select(this).classed("selected", true);
                selectedNode = nodeId;
                
                // Show node details
                showNodeDetails(nodeData.node, nodeData.index);
                
                // Prevent event bubbling
                event.stopPropagation();
            });

        // Setup edge hover for tooltips
        svgGroup.selectAll("g.edgePath")
            .append("title")
            .text(function() {
                const edgeObj = g.edge(this.parentNode.__data__);
                return edgeObj.tooltip || edgeObj.label;
            });

        // Clear selection when clicking on the background
        svg.on("click", () => {
            if (selectedNode) {
                svgGroup.select(`#${selectedNode}`)
                    .classed("selected", false);
                selectedNode = null;
                nodeInfo.classList.add('hidden');
            }
        });
        
        // Set up custom tooltips for better visualization
        setupCustomTooltips();
    }

    function setupCustomTooltips() {
        // Create tooltip element
        const tooltip = document.createElement('div');
        tooltip.className = 'tooltip';
        tooltip.style.display = 'none';
        document.body.appendChild(tooltip);
        
        // Add event listeners to edges
        svgGroup.selectAll("g.edgePath")
            .on("mouseover", function(event) {
                const edgeData = g.edge(this.__data__);
                if (edgeData && edgeData.tooltip) {
                    tooltip.textContent = edgeData.tooltip;
                    tooltip.style.display = 'block';
                    tooltip.style.left = `${event.pageX + 10}px`;
                    tooltip.style.top = `${event.pageY + 10}px`;
                }
            })
            .on("mousemove", function(event) {
                tooltip.style.left = `${event.pageX + 10}px`;
                tooltip.style.top = `${event.pageY + 10}px`;
            })
            .on("mouseout", function() {
                tooltip.style.display = 'none';
            });
    }

    function showNodeDetails(node, index) {
        nodeInfo.classList.remove('hidden');
        nodeDetails.innerHTML = '';
        
        const funcName = getFunctionName(node.func_idx);
        
        const titleElem = document.createElement('h4');
        titleElem.textContent = `${funcName} (Node ${index})`;
        nodeDetails.appendChild(titleElem);
        
        // Function details if available in PSpec
        if (pspec && pspec.endpoints && node.func_idx < pspec.endpoints.length) {
            const endpoint = pspec.endpoints[node.func_idx];
            
            // Template if available
            if (endpoint.template) {
                const templateElem = document.createElement('div');
                templateElem.className = 'function-template';
                templateElem.textContent = endpoint.template;
                nodeDetails.appendChild(templateElem);
                
                const hr = document.createElement('hr');
                nodeDetails.appendChild(hr);
            }
        }
        
        // Inputs
        if (node.inputs && node.inputs.length > 0) {
            const inputsTitle = document.createElement('h5');
            inputsTitle.textContent = 'Inputs:';
            nodeDetails.appendChild(inputsTitle);
            
            const inputsList = document.createElement('ul');
            node.inputs.forEach((input, idx) => {
                const li = document.createElement('li');
                const varName = getVariableName(node.func_idx, true, idx);
                const objectName = getObjectName(input.object_idx);
                li.textContent = `${varName}: ${objectName} from Node ${input.other_idx}`;
                inputsList.appendChild(li);
            });
            nodeDetails.appendChild(inputsList);
        }
        
        // Outputs
        if (node.outputs && node.outputs.length > 0) {
            const outputsTitle = document.createElement('h5');
            outputsTitle.textContent = 'Outputs:';
            nodeDetails.appendChild(outputsTitle);
            
            const outputsList = document.createElement('ul');
            node.outputs.forEach((output, idx) => {
                const li = document.createElement('li');
                const varName = getVariableName(node.func_idx, false, idx);
                const objectName = getObjectName(output.object_idx);
                
                // Check if this is a connected output
                if (output.other_idx !== undefined && 
                    output.other_idx !== null && 
                    output.other_idx !== Number.MAX_SAFE_INTEGER) {
                    li.textContent = `${varName}: ${objectName} to Node ${output.other_idx}`;
                } else {
                    li.textContent = `${varName}: ${objectName} (Disconnected)`;
                }
                
                outputsList.appendChild(li);
            });
            nodeDetails.appendChild(outputsList);
        }
        
        // Object attributes if available
        if (pspec && node.inputs && node.inputs.some(input => input.attrs && input.attrs.length > 0)) {
            const attrsTitle = document.createElement('h5');
            attrsTitle.textContent = 'Object Attributes:';
            nodeDetails.appendChild(attrsTitle);
            
            const attrsList = document.createElement('ul');
            
            node.inputs.forEach((input, idx) => {
                if (input.attrs && input.attrs.length > 0) {
                    const objectName = getObjectName(input.object_idx);
                    const li = document.createElement('li');
                    li.textContent = `${objectName}: ${formatAttributes(input.attrs)}`;
                    attrsList.appendChild(li);
                }
            });
            
            node.outputs.forEach((output, idx) => {
                if (output.attrs && output.attrs.length > 0) {
                    const objectName = getObjectName(output.object_idx);
                    const li = document.createElement('li');
                    li.textContent = `${objectName}: ${formatAttributes(output.attrs)}`;
                    attrsList.appendChild(li);
                }
            });
            
            nodeDetails.appendChild(attrsList);
        }
        
        // Preconditions if available
        if (pspec && pspec.endpoints && node.func_idx < pspec.endpoints.length) {
            const endpoint = pspec.endpoints[node.func_idx];
            
            if (endpoint.preconditions && endpoint.preconditions.length > 0) {
                const precondTitle = document.createElement('h5');
                precondTitle.textContent = 'Preconditions:';
                nodeDetails.appendChild(precondTitle);
                
                const precondList = document.createElement('ul');
                endpoint.preconditions.forEach(precond => {
                    if (precond.expr) {
                        const li = document.createElement('li');
                        li.textContent = precond.expr;
                        precondList.appendChild(li);
                    }
                });
                
                if (precondList.children.length > 0) {
                    nodeDetails.appendChild(precondList);
                }
            }
            
            // Effects if available
            if (endpoint.effects && endpoint.effects.length > 0) {
                const effectsTitle = document.createElement('h5');
                effectsTitle.textContent = 'Effects:';
                nodeDetails.appendChild(effectsTitle);
                
                const effectsList = document.createElement('ul');
                endpoint.effects.forEach(effect => {
                    const li = document.createElement('li');
                    li.textContent = effect;
                    effectsList.appendChild(li);
                });
                
                nodeDetails.appendChild(effectsList);
            }
        }
    }

    function formatAttributes(attrs) {
        return attrs.map(attr => {
            if (typeof attr === 'object') {
                return `${attr.name}: ${attr.value}`;
            }
            return attr;
        }).join(', ');
    }

    function downloadSVG() {
        if (!svg) return;
        
        // Clone the SVG to make modifications
        const svgEl = svg.node();
        const clonedSvg = svgEl.cloneNode(true);
        
        // Add proper XML namespaces
        clonedSvg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
        clonedSvg.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink');
        
        // Set fixed dimensions
        const bbox = svgGroup.node().getBBox();
        clonedSvg.setAttribute('width', bbox.width + 100);
        clonedSvg.setAttribute('height', bbox.height + 100);
        
        // Adjust the viewBox to focus on the graph content
        clonedSvg.setAttribute('viewBox', `${bbox.x - 50} ${bbox.y - 50} ${bbox.width + 100} ${bbox.height + 100}`);
        
        // Remove any transform from the main group to avoid double-transformation
        const mainGroup = clonedSvg.querySelector('g');
        if (mainGroup) {
            mainGroup.setAttribute('transform', '');
        }
        
        // Convert SVG to string
        const serializer = new XMLSerializer();
        const svgString = serializer.serializeToString(clonedSvg);
        
        // Create a blob from the SVG string
        const blob = new Blob([svgString], { type: 'image/svg+xml' });
        const url = URL.createObjectURL(blob);
        
        // Create a link to download the SVG
        const a = document.createElement('a');
        a.href = url;
        a.download = 'sgraph.svg';
        document.body.appendChild(a);
        a.click();
        
        // Clean up
        setTimeout(() => {
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }, 0);
    }
    
    // Utility function for debouncing
    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // Helper function to determine function type for styling
    function getFunctionType(funcIdx) {
        if (!pspec || !pspec.endpoints || funcIdx >= pspec.endpoints.length) {
            return 'function';
        }
        
        const endpoint = pspec.endpoints[funcIdx];
        
        // Check variable directions to determine function type
        if (endpoint.variables) {
            // Check if it's only creating new objects (constructor)
            const isConstructor = endpoint.variables.every(v => v.dir === 'new');
            if (isConstructor) {
                return 'constructor';
            }
            
            // Check if it's only destroying objects (destructor)
            const isDestructor = endpoint.variables.every(v => v.dir === 'del');
            if (isDestructor) {
                return 'destructor';
            }
        }
        
        // Default function type
        return 'function';
    }

    // Helper function to get detailed object info
    function getObjectDetails(objectIdx) {
        if (!pspec || !pspec.objects || objectIdx >= pspec.objects.length) {
            return `Object ${objectIdx}`;
        }
        
        const object = pspec.objects[objectIdx];
        const attrs = object.attrs.map(attr => {
            if (attr.type === 'enum' && attr.variants) {
                return `${attr.name}: enum(${attr.variants.join(', ')})`;
            } else {
                return `${attr.name}: ${attr.type}`;
            }
        }).join('\n');
        
        return `${object.name}\n${attrs}`;
    }
}); 