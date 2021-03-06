import sys
import os
import json
import argparse
import time
import numpy as np

sys.path.append("../pythonUtils/")
import utilities as utl

import ROOT
ROOT.gROOT.SetBatch(True)


## Custom C++ function to be used in Define
ROOT.gInterpreter.Declare("""
Float_t DeltaPhiMinN(int NjetsMax, ROOT::VecOps::RVec<Float_t>& phi, Float_t& phi2) {
    ROOT::VecOps::RVec<Float_t> dPhi;
    int size = (int)phi.size();
    auto idxMax = std::min(size, NjetsMax);

    for (auto idx=0; idx<idxMax; idx++) {
        dPhi.push_back(ROOT::VecOps::DeltaPhi(phi[idx], phi2));
    }

    return(ROOT::VecOps::Min(dPhi));
}
""")



## Useful functions

# Book a histogram for a specific variable
def bookHistogram(df, variable, range_, weight):
    return df.Histo1D(ROOT.ROOT.RDF.TH1DModel(variable, variable, range_[0], range_[1], range_[2]), variable, weight)

# Write a histogram with a given name to the output ROOT file
def writeHistogram(h, name):
    h.SetName(name)
    h.Write()


## Main function making histograms

def main(MODE, variables, binning, sample, outputDirectory, LUMI, N_EVTS_MAX_PER_BATCH, VERBOSE):

    # Set up multi-threading capability of ROOT
    ROOT.ROOT.EnableImplicitMT()

    # Get sample name and cross-section
    sampleName = sample["name"]
    XSection = sample["XSection"]

    # Create/update output file
    ROOTfileName = outputDirectory + sampleName + ".root"
    if VERBOSE>0: print("\nWill %s ROOT file %s." %(MODE.lower(), ROOTfileName))
    tfile = ROOT.TFile(ROOTfileName, MODE)

    # Initialise event counter
    nGenEvts = 0

    # Make batches of files with a total of less than X million events
    # Need to do that because RDataFrame efficient features seems to break down
    # for too many events at once
    batches = [[]]
    nEvts = 0
    nFilesTot = len(sample["fileset"])
    for ifile, file_ in enumerate(sample["fileset"]):

        # If file is on eos, add global redirector
        if file_.startswith("/store/mc/") or file_.startswith("/store/user/"):
            file_ = "root://cms-xrd-global.cern.ch/" + file_

        f = ROOT.TFile.Open(file_, "READ")
        t = f.Get("Events")
        nEvtsFile = t.GetEntries()
        if nEvts+nEvtsFile < N_EVTS_MAX_PER_BATCH:
            batches[-1].append(file_)
            nEvts += nEvtsFile
        else:
            if len(batches[-1]) > 0:
                nEvts = nEvtsFile
                batches.append([file_])
            else:
                if VERBOSE>0: 
                    print("WARNING: More than %d events in file %s" %(N_EVTS_MAX_PER_BATCH, file_))
                    print("         Creating batch with 1 file, exceeding maximum number of events per batch.")
                nEvts = nEvtsFile
                batches[-1].append(file_)
                if ifile+1 != nFilesTot: batches.append([])

    if VERBOSE>0: print("%d batches of files made" %(len(batches)))

    # Object to store histograms from the different batches of file sets
    hists = []


    # Loop over all batches of files
    for fileset in batches:
        if VERBOSE>0: print("")

        # Object to store histograms from the different files in this batch
        histsBatch = []

        # Loop over all files
        nFiles = len(fileset)
        for iFile, fileName in enumerate(fileset):

            tstart = time.time()
            if VERBOSE>0: print("Reading file %d out of %d" %(iFile+1, nFiles))

            histsBatch.append({})

            # If file is on eos, add global redirector
            if fileName.startswith("/store/mc/") or fileName.startswith("/store/user/"):
                fileName = "root://cms-xrd-global.cern.ch/" + fileName

            # Lists to store pointers to different RDataFrames (different filters)
            # and to store variables that have been defined
            dfList = []
            definedVars = []


            ## Make RDataFrames with all requested variables
            for idf, dataframe in enumerate(dataframes):

                # Check ordering of dataframes read
                dfIdx = dataframe["idx"]
                if dfIdx != idf:
                    print("ERROR: Mismatch between dataframe index and the number of dataframes defined so far.")
                    break

                # Define dataframe
                # Either read ROOT file (1st dataframe)
                if dfIdx == 0:
                    dfList.append(ROOT.ROOT.RDataFrame("Events", fileName))
                    # Increment the number of events processed
                    nGenEvts += dfList[0].Sum("genWeight").GetValue()  
                # Or build it from filtering an already defined dataframe
                else:
                    idxBase = dataframe["idxBase"]
                    f1 = dataframe["filter"][0]
                    f2 = dataframe["filter"][1]
                    dfList.append(dfList[idxBase].Filter(f1 , f2))

                # Define variables in this dataframe
                definedVars.append([])
                for define in dataframe["defines"]:
                    variable = define[0]
                    definition = define[1]
                    dfList[dfIdx] = dfList[dfIdx].Define(variable, definition)
                    definedVars[dfIdx].append(variable)

           
            ## Book histograms
            for variable in variables:

                # Check if binning is defined for this variable
                if variable not in binning["noregex"]:
                    regexes = list(binning["regex"].keys())
                    indices = utl.inregex(variable, regexes)
                    if len(indices) == 0:
                        if VERBOSE>0: print("WARNING: Binning of %s is not defined. Skipping." %variable)
                        continue
                    elif len(indices) > 1:
                        if VERBOSE>0: print("WARNING: %s matches several regexes. Binning cannot be defined. Skipping." %variable)
                        continue
                    else: 
                        binning_ = binning["regex"][regexes[indices[0]]]
                else:
                    binning_ = binning["noregex"][variable]

                # Find from which RDataFrame the variable has been defined
                for idx in range(len(definedVars)):
                    if variable in definedVars[idx]:
                        break
                    else:
                        # If variable is not defined, then it's taken from the uncut dataframe
                        if idx == len(definedVars)-1:
                            idx = 0

                # Check if variable present in dataframe
                if not variable in dfList[idx].GetColumnNames():
                    if VERBOSE>0: print("WARNING: Variable %s is not in dataframe (index %d). Will not be saved to output ROOT file." %(variable, idx))
                    continue
                
                # Book histogram
                # Histograms should NOT be added together yet as RDataFrame would not proceed in one loop in an efficient way
                # Should be done at the very end
                weight = "genWeight"
                histsBatch[iFile][variable] = bookHistogram(dfList[idx], variable, binning_, weight)


            ## For sanity check, print defined variables not asked to be saved in histogram
            definedVarsFlat = []
            for idx in range(len(definedVars)):
                definedVarsFlat = definedVarsFlat + definedVars[idx]
            unsavedVars = [ variable for variable in definedVarsFlat if variable not in variables ]
            if len(unsavedVars) > 0 and VERBOSE>1:
                print("INFO: The following variable were defined but not instructed to be saved in ROOT file:")
                for x in unsavedVars: print("\t%s" %x)
                print("")

            elapsed = time.time() - tstart
            if VERBOSE>0: print("Elapsed time: %d s" %elapsed)


        ## Adding histograms of the batch together
        if VERBOSE>0: print("Adding together histograms of batch %d..." %(len(hists)+1))
        tstrat = time.time()
        hists.append({})
        for variable in histsBatch[0].keys():
            hists[-1][variable] = histsBatch[0][variable]
            for iFile in range(1, len(histsBatch)):
                hists[-1][variable].Add(histsBatch[iFile][variable].GetValue())
        elapsed = time.time() - tstart
        if VERBOSE>0: print("Elapsed time: %d s" %elapsed)


    ## Normalize histograms and write to output ROOT file
    if VERBOSE>0: print("\nNormalizing histograms and writing to output ROOT file...")
    tstart = time.time()
    tfile.cd()
    for variable in hists[0].keys():
        hist = hists[0][variable]
        for iFile in range(1, len(hists)):
            hist.Add(hists[iFile][variable].GetValue())
        hist.Scale( XSection*LUMI/nGenEvts )
        writeHistogram(hist, "{}".format(variable))
        if VERBOSE>1: print("%s histogram saved" %variable)
    elapsed = time.time() - tstart
    if VERBOSE>0: print("Elapsed time: %d s" %elapsed)

    tfile.Close()



if __name__ == "__main__":

    tstart = time.time()

    ## Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-m", "--mode",
        choices=["RECREATE", "UPDATE"],
        help="Mode in which to open the output ROOT file"
        )
    parser.add_argument(
        "-var", "--variables",
        help="json file listing variables to save or comma separated variable names"
        )
    parser.add_argument(
        "-d", "--dataframes",
        help="json file describing filters and defines of the RDataFrames needed to comoute the required variables"
        )
    parser.add_argument(
        "-b", "--binning",
        help="json file describing binning of the histograms"
        )
    parser.add_argument(
        "-sd", "--samplesDescription",
        help="json file describing samples location"
        )
    parser.add_argument(
        "-s", "--samples",
        help="Comma separated list samples to pick up among the samples described in the sample file"
        )
    parser.add_argument(
        "-o", "--outputDirectory",
        nargs="?",
        const="./",
        help="Path to the directory where to recreate/update ROOT file"
        )
    parser.add_argument(
        "-l", "--lumi",
        nargs="?",
        default=59725.0,   # 21071.0+38654.0
        help="Total luminosity for normalization of the histograms"
        )
    parser.add_argument(
        "-nevts", "--nEvtsMaxPerBatch",
        nargs="?",
        default=5e6,
        help="Maximum number of events per batches. RDT efficient features break down for too\
              many events at once. Batches of files with limited number of events are made."
        )
    parser.add_argument(
        "-v", "--verbose",
        choices=["0", "1", "2"],
        nargs="?",
        default="1",
        help="Verbose level"
        )

    args = parser.parse_args()

    
    # Create output directory if does not exist
    outputDir = args.outputDirectory
    if not os.path.exists(outputDir):
        os.makedirs(outputDir)

    # All samples description
    samplesDescription = utl.makeJsonData(args.samplesDescription)

    # Variables to histogram
    if args.variables.endswith(".json"):
        with open(args.variables, 'r') as f:
            variables = json.load(f)["variables"]
    else:
        variables = args.variables.split(",")

    # Define the binning of the different variables to histogram
    with open(args.binning, 'r') as f:
        binning = json.load(f)["binning"]

    # Read filters and defines instructions to build up sequentially RDataFrames
    with open(args.dataframes, 'r') as f:
        dataframes = json.load(f)["dataframes"]

    # Get samples for which to make histograms
    if not args.samples:
        samplesNames = list(samplesDescription.keys())
    else:
        samplesNames = args.samples.split(",")

    # Loop over all samples
    for idx, sampleName in enumerate(samplesNames):
        sample = samplesDescription[sampleName]
        if "name" not in sample.keys():
            sample["name"] = sampleName
        # and make histograms
        main(args.mode, variables, binning, sample, outputDir, float(args.lumi), float(args.nEvtsMaxPerBatch), int(args.verbose))


    elapsed = time.time() - tstart
    print("\nTotal elapsed time: %d s" %elapsed)
