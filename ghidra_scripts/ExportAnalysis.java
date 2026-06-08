// Exports every non-thunk function in the current program to JSON.
// Output: data/ghidra_json/<binary_name>.json (relative to analyzeHeadless cwd)
//
// Requires Ghidra 10.x+ (Gson is bundled).
//
//@category Analysis

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.decompiler.DecompiledFunction;
import ghidra.program.model.listing.*;
import ghidra.program.model.pcode.*;
import ghidra.program.model.block.*;
import ghidra.program.model.address.*;
import ghidra.program.model.symbol.*;

import com.google.gson.*;
import java.io.*;
import java.util.*;

public class ExportAnalysis extends GhidraScript {

    private DecompInterface decompiler;
    private Listing listing;

    @Override
    public void run() throws Exception {
        listing = currentProgram.getListing();

        DecompileOptions opts = new DecompileOptions();
        decompiler = new DecompInterface();
        decompiler.setOptions(opts);
        decompiler.openProgram(currentProgram);

        JsonArray output = new JsonArray();
        FunctionIterator funcs = currentProgram.getFunctionManager().getFunctions(true);
        int total = 0, skipped = 0;

        while (funcs.hasNext()) {
            if (monitor.isCancelled()) break;
            Function func = funcs.next();

            // Skip thunks and externals — they have no real body to analyze
            if (func.isThunk() || func.isExternal()) { skipped++; continue; }

            try {
                output.add(exportFunction(func));
                total++;
            } catch (Exception e) {
                println("WARNING: skipping " + func.getName() + ": " + e.getMessage());
                skipped++;
            }
        }

        decompiler.dispose();

        // Output directory: prefer the absolute path passed as a script argument,
        // fall back to a relative path (useful when running the script manually
        // from inside Ghidra's GUI Script Manager).
        String[] scriptArgs = getScriptArgs();
        String outputDirPath = (scriptArgs != null && scriptArgs.length > 0)
            ? scriptArgs[0]
            : "data/ghidra_json";

        File outDir = new File(outputDirPath);
        outDir.mkdirs();

        String safeName = currentProgram.getName().replaceAll("[^a-zA-Z0-9._-]", "_");
        File outFile = new File(outDir, safeName + ".json");

        Gson gson = new GsonBuilder().setPrettyPrinting().disableHtmlEscaping().create();
        try (FileWriter fw = new FileWriter(outFile)) {
            fw.write(gson.toJson(output));
        }

        println("=== Export complete ===");
        println("Functions exported : " + total);
        println("Functions skipped  : " + skipped);
        println("Output             : " + outFile.getAbsolutePath());
    }

    // -------------------------------------------------------------------------
    // Function export
    // -------------------------------------------------------------------------

    private JsonObject exportFunction(Function func) throws Exception {
        JsonObject obj = new JsonObject();

        obj.addProperty("name",      func.getName());
        obj.addProperty("address",   func.getEntryPoint().toString());
        obj.addProperty("signature", func.getSignature().getPrototypeString());

        // Parameters
        obj.add("parameters", exportParameters(func));

        // Call graph neighbours
        obj.add("callees", namesToArray(func.getCalledFunctions(monitor)));
        obj.add("callers", namesToArray(func.getCallingFunctions(monitor)));

        // Imports called by this function (external / thunk targets)
        obj.add("imports", exportImports(func));

        // Decompile (C + high p-code)
        DecompileResults dr = decompiler.decompileFunction(func, 60, monitor);
        if (dr != null && dr.decompileCompleted()) {
            DecompiledFunction df = dr.getDecompiledFunction();
            obj.addProperty("decompiled", df != null ? df.getC() : "");
            obj.add("pcode", exportPcode(dr.getHighFunction()));
        } else {
            obj.addProperty("decompiled", "");
            obj.add("pcode", new JsonArray());
        }

        // CFG
        obj.add("cfg", exportCFG(func));

        // Strings referenced by this function
        obj.add("strings", exportStrings(func));

        return obj;
    }

    // -------------------------------------------------------------------------
    // Parameters
    // -------------------------------------------------------------------------

    private JsonArray exportParameters(Function func) {
        JsonArray arr = new JsonArray();
        for (Parameter p : func.getParameters()) {
            JsonObject po = new JsonObject();
            po.addProperty("name", p.getName());
            po.addProperty("type", p.getDataType().getDisplayName());
            arr.add(po);
        }
        return arr;
    }

    // -------------------------------------------------------------------------
    // P-code (from decompiler's high-level SSA representation)
    // -------------------------------------------------------------------------

    private JsonArray exportPcode(HighFunction high) {
        JsonArray arr = new JsonArray();
        if (high == null) return arr;

        Iterator<PcodeOpAST> ops = high.getPcodeOps();
        while (ops.hasNext()) {
            arr.add(ops.next().toString());
        }
        return arr;
    }

    // -------------------------------------------------------------------------
    // CFG — basic blocks + edges
    // -------------------------------------------------------------------------

    private JsonObject exportCFG(Function func) throws Exception {
        JsonObject cfg = new JsonObject();
        JsonArray blocks = new JsonArray();
        JsonArray edges  = new JsonArray();

        BasicBlockModel bbModel = new BasicBlockModel(currentProgram);
        CodeBlockIterator it = bbModel.getCodeBlocksContaining(func.getBody(), monitor);

        // Collect all blocks first (iterator is single-pass)
        List<CodeBlock> blockList = new ArrayList<>();
        while (it.hasNext()) {
            blockList.add(it.next());
        }

        for (CodeBlock block : blockList) {
            String blockAddr = block.getFirstStartAddress().toString();

            // Block object
            JsonObject bo = new JsonObject();
            bo.addProperty("address", blockAddr);
            bo.addProperty("end",     block.getMaxAddress().toString());

            // Raw instructions (assembly) inside the block
            JsonArray instrs = new JsonArray();
            InstructionIterator ii = listing.getInstructions(block, true);
            while (ii.hasNext()) {
                instrs.add(ii.next().toString());
            }
            bo.add("instructions", instrs);
            blocks.add(bo);

            // Outgoing edges
            CodeBlockReferenceIterator dests = block.getDestinations(monitor);
            while (dests.hasNext()) {
                CodeBlockReference ref = dests.next();
                CodeBlock dest = ref.getDestinationBlock();
                if (dest == null) continue;

                JsonObject edge = new JsonObject();
                edge.addProperty("from",     blockAddr);
                edge.addProperty("to",       dest.getFirstStartAddress().toString());
                edge.addProperty("flowType", ref.getFlowType().toString());
                edges.add(edge);
            }
        }

        cfg.add("blocks", blocks);
        cfg.add("edges",  edges);
        return cfg;
    }

    // -------------------------------------------------------------------------
    // Strings referenced inside this function
    // -------------------------------------------------------------------------

    private JsonArray exportStrings(Function func) {
        JsonArray arr = new JsonArray();
        Set<String> seen = new LinkedHashSet<>();

        InstructionIterator ii = listing.getInstructions(func.getBody(), true);
        while (ii.hasNext()) {
            Instruction instr = ii.next();
            for (Reference ref : instr.getReferencesFrom()) {
                if (!ref.getReferenceType().isData()) continue;
                Data data = listing.getDataAt(ref.getToAddress());
                if (data == null) continue;
                Object val = data.getValue();
                if (val instanceof String) {
                    String s = (String) val;
                    if (s.length() > 1 && seen.add(s)) {
                        arr.add(s);
                    }
                }
            }
        }
        return arr;
    }

    // -------------------------------------------------------------------------
    // Import names (external / thunk callees only)
    // -------------------------------------------------------------------------

    private JsonArray exportImports(Function func) throws Exception {
        JsonArray arr = new JsonArray();
        Set<String> seen = new LinkedHashSet<>();
        for (Function callee : func.getCalledFunctions(monitor)) {
            if ((callee.isExternal() || callee.isThunk()) && seen.add(callee.getName())) {
                arr.add(callee.getName());
            }
        }
        return arr;
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    private JsonArray namesToArray(Set<Function> funcs) {
        JsonArray arr = new JsonArray();
        for (Function f : funcs) {
            arr.add(f.getName());
        }
        return arr;
    }
}
